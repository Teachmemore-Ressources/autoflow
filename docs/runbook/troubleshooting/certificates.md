---
title: Dépannage Certificats
---

# Dépannage Certificats & TLS

---

## Problème 1 — Certificat expiré

**Symptôme** : `ERR_CERT_DATE_INVALID` dans le navigateur, erreurs TLS dans les logs.

**Diagnostic** :
```bash
# Vérifier l'expiration d'un service
echo | openssl s_client -connect awx.<DOMAIN>:443 -servername awx.<DOMAIN> 2>/dev/null \
  | openssl x509 -noout -dates

# Vérifier tous les endpoints
for svc in awx git pki monitoring api; do
  echo -n "$svc.<DOMAIN>: "
  echo | openssl s_client -connect $svc.<DOMAIN>:443 -servername $svc.<DOMAIN> 2>/dev/null \
    | openssl x509 -noout -enddate 2>/dev/null || echo "ERREUR"
done
```

**Solutions** :

=== "PKI interne (cas normal)"

    ```bash
    # Accéder à l'interface PKI
    # https://pki.<DOMAIN>
    
    # Via API PKI
    curl -X POST https://pki.<DOMAIN>/api/v1/certificates/renew \
      -H "Authorization: Bearer $PKI_JWT_SECRET" \
      -d '{"hostname": "awx.<DOMAIN>"}'
    
    # Redémarrer Traefik pour charger le nouveau certificat
    docker compose restart traefik
    ```

=== "Let's Encrypt (si configuré)"

    ```bash
    # Forcer le renouvellement
    docker compose stop traefik
    
    # Supprimer le fichier ACME corrompu ou expiré
    rm traefik/acme.json
    touch traefik/acme.json
    chmod 600 traefik/acme.json
    
    docker compose start traefik
    # Traefik va automatiquement demander de nouveaux certificats
    ```

---

## Problème 2 — Certificat non reconnu (NET::ERR_CERT_AUTHORITY_INVALID)

**Symptôme** : Le navigateur affiche une erreur de certificat malgré un cert valide.

**Cause** : Le certificat CA interne n'est pas dans le trust store du navigateur/OS.

**Solutions** :

=== "Linux (système)"

    ```bash
    # Récupérer le CA cert
    curl -sk https://pki.<DOMAIN>/api/v1/ca/cert -o /tmp/autoflow-ca.crt
    
    # Ubuntu/Debian
    sudo cp /tmp/autoflow-ca.crt /usr/local/share/ca-certificates/autoflow-ca.crt
    sudo update-ca-certificates
    
    # RHEL/CentOS
    sudo cp /tmp/autoflow-ca.crt /etc/pki/ca-trust/source/anchors/autoflow-ca.crt
    sudo update-ca-trust
    ```

=== "macOS"

    ```bash
    # Télécharger le CA cert, puis :
    sudo security add-trusted-cert -d -r trustRoot \
      -k /Library/Keychains/System.keychain /tmp/autoflow-ca.crt
    ```

=== "Windows"

    Clic droit sur le fichier `.crt` → **Installer le certificat** → **Ordinateur local** → **Autorités de certification racines de confiance**.

=== "Docker / Containers"

    ```bash
    # Ajouter le CA dans un container
    docker cp /tmp/autoflow-ca.crt autoflow_awx_task:/usr/local/share/ca-certificates/
    docker exec autoflow_awx_task update-ca-certificates
    ```

---

## Problème 3 — Alerte "Certificate expiring in X days" dans Prometheus

**Diagnostic** :
```bash
# Voir les alertes d'expiration actives
curl -sf http://localhost:9090/api/v1/query?query=ssl_cert_not_after \
  | python3 -m json.tool | grep -B2 "value"

# Requête pour identifier les certs expirant dans 30j
curl -sf "http://localhost:9090/api/v1/query?query=(ssl_cert_not_after-time())%2F86400+<+30" \
  | python3 -m json.tool
```

**Actions préventives** :
```bash
# Renouveler via PKI (30 jours avant expiration recommandé)
curl -X POST https://pki.<DOMAIN>/api/v1/certificates/renew \
  -H "Authorization: Bearer $PKI_JWT_SECRET" \
  -d '{"hostname": "<HOSTNAME>"}'

docker compose restart traefik
```

---

## Problème 4 — Traefik ne charge pas le certificat

**Diagnostic** :
```bash
# Voir si Traefik trouve le fichier de certificat
docker logs autoflow_traefik 2>&1 | grep -i "cert\|tls\|error" | tail -20

# Vérifier les fichiers montés
docker inspect autoflow_traefik | grep -A10 '"Mounts"'

# Vérifier la config TLS
docker exec autoflow_traefik cat /etc/traefik/dynamic/tls.yml
```

**Solutions** :

```bash
# Si le fichier cert n'existe pas
ls -la traefik/certs/

# Si les permissions sont mauvaises
chmod 644 traefik/certs/*.crt
chmod 600 traefik/certs/*.key

# Redémarrer Traefik
docker compose restart traefik
docker logs autoflow_traefik --tail=20 | grep -i "tls\|cert"
```

---

## Problème 5 — Erreur TLS entre containers (AWX → Gitea)

**Symptôme** : AWX ne peut pas cloner depuis Gitea avec HTTPS.

**Diagnostic** :
```bash
# Tester depuis awx_task
docker compose exec awx_task curl -v https://git.<DOMAIN>/api/v1/repos/search 2>&1 | grep -E "SSL|certificate|error"
```

**Solutions** :

```bash
# Option 1 : Ajouter le CA cert dans AWX
# Copier le CA dans l'image/container AWX
docker compose exec awx_task bash -c \
  "curl -sk https://pki.<DOMAIN>/api/v1/ca/cert -o /etc/ssl/certs/autoflow-ca.crt && update-ca-certificates"

# Option 2 : Utiliser l'URL interne Docker (HTTP) pour les connexions intra-réseau
# (uniquement pour le réseau Docker interne, pas pour l'accès public)

# Option 3 (dev uniquement) : désactiver la vérification SSL dans AWX
# AWX UI : Settings → Jobs → Extra Environment Variables
# {"GIT_SSL_NO_VERIFY": "true"}
```

---

## Problème 6b — `error setting certificate file: /etc/autoflow/ca.crt` lors d'un git sync AWX

**Symptôme** : Un job de mise à jour de projet échoue avec :

```
fatal: unable to access 'https://git.<DOMAIN>/...': error setting certificate file: /etc/autoflow/ca.crt
```

**Cause A — Le fichier CA a été remplacé par un répertoire vide (ghost directory)**

Lorsque Docker tente de bind-monter un fichier source inexistant, il crée un répertoire vide à la place.
Ce répertoire bloque tous les lancements de jobs suivants même après restauration du fichier, car le container doit être recréé.

**Diagnostic** :
```bash
# Sur l'hôte : vérifier que le fichier n'est pas un répertoire
ls -la traefik/certs/ca.<DOMAIN>.crt
# Si "drwxr-xr-x" → ghost directory → suivre la procédure ci-dessous
```

**Correction** :
```bash
# 1. Supprimer le ghost directory
sudo rm -rf traefik/certs/ca.<DOMAIN>.crt

# 2. Restaurer le cert CA depuis le volume PKI
docker run --rm \
  -v autoflow_pki_data:/data \
  -v $(pwd)/traefik/certs:/out \
  busybox cp /data/ca/autoflow-root_cert.pem /out/ca.<DOMAIN>.crt

# 3. Vérifier que c'est bien un fichier PEM
file traefik/certs/ca.<DOMAIN>.crt
# → doit afficher "PEM certificate"

# 4. Forcer la recréation des containers AWX (obligatoire — Docker ne remonte pas automatiquement)
docker compose up -d --force-recreate awx_web awx_task
```

**Cause B — Le fichier CA a disparu après un `git pull`**

Si le cert avait été tracké en git et qu'un commit de `git rm --cached` a été poussé, le `git pull` suivant le supprime physiquement du disque.

```bash
# Vérifier dans l'historique git si le cert a été supprimé
git log --oneline --diff-filter=D -- 'traefik/certs/*.crt'

# Restaurer depuis le volume PKI (même procédure que Cause A ci-dessus)
# Puis s'assurer que traefik/certs/*.crt est bien dans .gitignore
grep 'traefik/certs' .gitignore
```

!!! tip "Prévention"
    Le container PKI (`autoflow_pki`) a un bind-mount sur `traefik/certs/`.
    Si le cert disparaît à nouveau, un simple redémarrage du PKI suffit à le réexporter :
    ```bash
    docker compose restart pki
    ls -la traefik/certs/ca.<DOMAIN>.crt  # doit réapparaître
    docker compose up -d --force-recreate awx_web awx_task
    ```

**Vérification finale** :
```bash
# Simuler le bind-mount Docker comme le fait AWX_ISOLATION_SHOW_PATHS
docker run --rm \
  -v traefik/certs/ca.<DOMAIN>.crt:/etc/autoflow/ca.crt:ro \
  busybox head -1 /etc/autoflow/ca.crt
# → doit afficher "-----BEGIN CERTIFICATE-----"

# Relancer un project update AWX pour confirmer
curl -sf -X POST https://awx.<DOMAIN>/api/v2/projects/<ID>/update/ \
  -u admin:<PASS> -H "Content-Type: application/json"
```

---

## Problème 6 — PKI inaccessible (impossible de renouveler)

```bash
# Vérifier PKI
docker compose ps pki
docker logs autoflow_pki --tail=20

# Redémarrer PKI
docker compose restart pki pki_postgres
sleep 15

# Test
curl -sf https://pki.<DOMAIN>/api/v1/health | python3 -m json.tool
```

---

## Vérifications TLS globales

```bash
# Audit TLS complet sur un endpoint (nécessite nmap)
nmap --script ssl-enum-ciphers -p 443 awx.<DOMAIN>

# Via sslyze (si installé)
sslyze --regular awx.<DOMAIN>:443

# Résumé openssl pour tous les services
for svc in awx git pki monitoring api; do
  echo "=== $svc.<DOMAIN> ==="
  echo | openssl s_client -connect $svc.<DOMAIN>:443 -servername $svc.<DOMAIN> 2>/dev/null \
    | openssl x509 -noout -subject -issuer -dates
  echo
done
```
