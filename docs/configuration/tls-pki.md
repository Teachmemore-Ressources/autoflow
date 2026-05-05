---
title: TLS & PKI
---

# TLS & PKI interne

Autoflow génère sa propre **Autorité de Certification (CA) interne** via le service `pki`. Tous les services HTTPS reçoivent des certificats signés par cette CA. Traefik présente ces certificats aux clients.

---

## Architecture PKI

```
CA Root (autoflow-root-ca)
  └── CA Intermédiaire (autoflow-intermediate-ca)
        ├── awx.<DOMAIN>
        ├── git.<DOMAIN>
        ├── api.<DOMAIN>
        ├── grafana.<DOMAIN>
        ├── prometheus.<DOMAIN>
        ├── alertmanager.<DOMAIN>
        └── pki.<DOMAIN>
```

Les certificats sont stockés dans `traefik/certs/` et montés dans Traefik.

---

## Configuration

| Variable | Description |
|---|---|
| `PKI_ADMIN_USER` | Username admin PKI |
| `PKI_ADMIN_PASSWORD` | Password admin PKI |
| `PKI_JWT_SECRET` | Clé signature JWT PKI — ne jamais changer après init |
| `PKI_BASE_URL` | URL publique PKI (auto-dérivée depuis DOMAIN) |
| `PKI_KEY_PASSPHRASE` | Passphrase clés privées (optionnel) |

!!! danger "PKI_JWT_SECRET"
    Cette clé signe les tokens d'authentification à la PKI. La changer invalide toutes les sessions actives. Générer une seule fois.

---

## Faire confiance au CA

### Sur le serveur (Docker + système)

```bash
make docker-trust-ca
```

Ce script :
1. Télécharge le certificat CA depuis le service PKI
2. L'installe dans `/usr/local/share/ca-certificates/`
3. Met à jour le trust store système (`update-ca-certificates`)
4. Configure Docker pour faire confiance au registry Gitea

### Sur les postes clients (navigateurs)

Télécharger le CA depuis `https://pki.<DOMAIN>/ca/download`, puis :

=== "Chrome / Edge"
    `Paramètres → Confidentialité et sécurité → Sécurité → Gérer les certificats → Autorités → Importer`

=== "Firefox"
    `Paramètres → Vie privée et sécurité → Certificats → Afficher les certificats → Autorités → Importer`

=== "macOS (Safari + Chrome)"
    ```bash
    sudo security add-trusted-cert -d -r trustRoot \
      -k /Library/Keychains/System.keychain ca.<DOMAIN>.crt
    ```

=== "Ubuntu / Debian"
    ```bash
    sudo cp ca.<DOMAIN>.crt /usr/local/share/ca-certificates/autoflow.crt
    sudo update-ca-certificates
    ```

=== "Windows"
    Double-clic sur le `.crt` → "Installer le certificat" → "Machine locale" → "Autorités de certification racines de confiance"

---

## Traefik & Certificats

Traefik lit les certificats depuis `traefik/certs/`. La configuration dynamique dans `traefik/dynamic/` référence ces fichiers.

```yaml
# traefik/dynamic/tls.yml (exemple généré)
tls:
  certificates:
    - certFile: /etc/traefik/certs/awx.<DOMAIN>.crt
      keyFile:  /etc/traefik/certs/awx.<DOMAIN>.key
    - certFile: /etc/traefik/certs/git.<DOMAIN>.crt
      keyFile:  /etc/traefik/certs/git.<DOMAIN>.key
```

---

## Renouvellement des certificats

Les certificats ont une durée de validité de **1 an** par défaut. Une alerte Prometheus se déclenche 30 jours avant expiration.

```bash
# Vérifier la date d'expiration d'un certificat
openssl x509 -in traefik/certs/awx.<DOMAIN>.crt -noout -enddate

# Régénérer via le service PKI (API)
curl -X POST https://pki.<DOMAIN>/api/certificates/renew \
  -H "Authorization: Bearer <PKI_JWT>" \
  -H "Content-Type: application/json" \
  -d '{"domain": "awx.<DOMAIN>"}'
```

---

## Intégration AWX — EE containers

Les conteneurs EE d'AWX ont besoin du CA pour cloner des projets depuis Gitea (HTTPS).

Dans `awx/settings.py`, `AWX_ISOLATION_SHOW_PATHS` monte le certificat CA dans chaque conteneur EE :

```python
AWX_ISOLATION_SHOW_PATHS = [
    f"{TRAEFIK_CERTS_DIR}/ca.{DOMAIN}.crt:/etc/autoflow/ca.crt:ro",
]
```

Et `DEFAULT_CONTAINER_RUN_OPTIONS` injecte les variables d'environnement :

```python
DEFAULT_CONTAINER_RUN_OPTIONS = [
    '--env', 'GIT_SSL_CAINFO=/etc/autoflow/ca.crt',
    '--env', 'SSL_CERT_FILE=/etc/autoflow/ca.crt',
]
```

Ainsi, git et Python dans les EE font automatiquement confiance au CA interne.
