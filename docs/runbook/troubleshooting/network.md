---
title: Dépannage Réseau
---

# Dépannage Réseau

---

## Problème 1 — Service injoignable de l'extérieur

**Symptôme** : `curl https://awx.<DOMAIN>` timeout ou "Connection refused".

**Diagnostic** :
```bash
# 1. Traefik est-il en marche ?
docker compose ps traefik

# 2. Les ports sont-ils ouverts sur l'hôte ?
ss -tlnp | grep -E "80|443"
# ou
netstat -tlnp | grep -E "80|443"

# 3. Traefik voit-il les routes ?
docker logs autoflow_traefik --tail=20 | grep -i "error\|warn"

# 4. Test local
curl -sk https://localhost/api/v2/ping/ -H "Host: awx.<DOMAIN>" | head -3
```

**Solutions** :

=== "Traefik pas démarré"

    ```bash
    docker compose up -d traefik
    sleep 5
    docker compose ps traefik
    ```

=== "Ports 80/443 occupés par un autre processus"

    ```bash
    # Identifier le processus sur le port 443
    sudo ss -tlnp | grep :443
    sudo lsof -i :443
    
    # Arrêter le service concurrent (nginx, apache, etc.)
    sudo systemctl stop nginx
    docker compose up -d traefik
    ```

=== "Firewall bloque le trafic"

    ```bash
    # Vérifier UFW
    sudo ufw status verbose
    
    # Ouvrir les ports si nécessaire
    sudo ufw allow 80/tcp
    sudo ufw allow 443/tcp
    sudo ufw reload
    ```

=== "DNS ne résout pas vers ce serveur"

    ```bash
    # Vérifier la résolution DNS depuis l'extérieur
    dig awx.<DOMAIN> @8.8.8.8
    
    # Vérifier l'IP du serveur
    curl -s ifconfig.me
    
    # Si incohérent : corriger l'enregistrement DNS
    ```

---

## Problème 2 — Réseau Docker interne cassé

**Symptôme** : Les containers ne se voient plus entre eux.

**Diagnostic** :
```bash
# Vérifier le réseau autoflow_net
docker network inspect autoflow_net | python3 -c \
  "import sys,json; [print(k, v.get('IPv4Address','?')) for k,v in json.load(sys.stdin)[0]['Containers'].items()]"

# Test de connectivité inter-container
docker compose exec event_engine ping -c 2 awx_web
docker compose exec event_engine curl -s http://awx_web:8052/health/ | head -3
```

**Solutions** :

```bash
# Si le réseau est dans un état incohérent
docker network disconnect autoflow_net <container_en_cause>
docker network connect autoflow_net <container_en_cause>

# Solution radicale : recréer tous les containers (préserve les volumes)
docker compose down
docker compose up -d

# Si le réseau Docker lui-même est corrompu
docker network rm autoflow_net
docker compose up -d  # Docker recrée automatiquement le réseau
```

---

## Problème 3 — Résolution DNS interne Docker échoue

**Symptôme** : Un container ne peut pas résoudre le nom d'un autre container.

**Diagnostic** :
```bash
# Tester depuis un container
docker compose exec event_engine nslookup awx_web
docker compose exec event_engine cat /etc/resolv.conf
```

**Solutions** :

```bash
# Vérifier que les containers sont sur le même réseau
docker inspect autoflow_event_engine | grep -A5 "Networks"
docker inspect autoflow_awx_web | grep -A5 "Networks"

# Si un container n'est pas sur autoflow_net
docker network connect autoflow_net <container>

# Si EE_DNS_SERVER est mal configuré (peut remplacer le DNS Docker)
grep EE_DNS_SERVER .env
# Si non vide et incorrect, vider la valeur
# EE_DNS_SERVER=
docker compose restart awx_web awx_task
```

---

## Problème 4 — Traefik ne route pas vers un service

**Symptôme** : HTTP 404 ou 502 sur un sous-domaine spécifique.

**Diagnostic** :
```bash
# Voir les routes Traefik actuelles
curl -sf http://localhost:8080/api/http/routers | python3 -m json.tool | grep '"name"'

# Voir les services backend
curl -sf http://localhost:8080/api/http/services | python3 -m json.tool | grep '"name"'

# Logs en temps réel
docker logs autoflow_traefik -f 2>&1 | grep -v "200\|304"
```

**Solutions** :

=== "Label Docker manquant ou incorrect"

    ```bash
    # Vérifier les labels du service concerné
    docker inspect autoflow_<service> | python3 -c \
      "import sys,json; labels=json.load(sys.stdin)[0]['Config']['Labels']; [print(k,v) for k,v in labels.items() if 'traefik' in k]"
    
    # Corriger dans docker-compose.yml et redémarrer
    docker compose up -d <service>
    ```

=== "Réseau Traefik mal configuré"

    ```bash
    # Traefik doit être sur autoflow_net pour atteindre les services
    docker inspect autoflow_traefik | grep -A5 "Networks"
    
    # Si manquant
    docker network connect autoflow_net autoflow_traefik
    docker compose restart traefik
    ```

=== "Fichier de config dynamic invalide"

    ```bash
    # Vérifier les fichiers dans traefik/dynamic/
    ls traefik/dynamic/
    
    # Valider la syntaxe YAML
    for f in traefik/dynamic/*.yml; do
      echo "Checking $f..."
      python3 -c "import yaml; yaml.safe_load(open('$f'))" && echo "OK" || echo "SYNTAX ERROR"
    done
    ```

---

## Problème 5 — Rate limiting déclenché abusivement

**Symptôme** : Erreurs HTTP 429 trop fréquentes.

**Diagnostic** :
```bash
# Voir les 429 dans les logs Traefik
docker logs autoflow_traefik 2>&1 | python3 -c "
import sys, json
for line in sys.stdin:
    try:
        d = json.loads(line)
        if d.get('DownstreamStatus') == 429:
            print(d.get('ClientAddr','?'), d.get('RequestPath','?'))
    except: pass
"
```

**Solution** :
```bash
# Ajuster la limite dans .env
# RATE_LIMIT=200/minute

docker compose up -d --force-recreate event_engine
```

---

## Problème 6 — SSH Git (port 2222) inaccessible

```bash
# Vérifier le port
ss -tlnp | grep 2222
docker compose ps gitea

# Test SSH
ssh -p 2222 -T git@<DOMAIN> -o ConnectTimeout=5

# Logs Gitea SSH
docker logs autoflow_gitea 2>&1 | grep -i "ssh\|error" | tail -10

# Firewall
sudo ufw status | grep 2222
sudo ufw allow 2222/tcp
```

---

## Outils de diagnostic réseau

```bash
# Depuis l'hôte — tester la connectivité vers un service interne
docker run --rm --network autoflow_net alpine \
  sh -c "nslookup awx_web && wget -qO- http://awx_web:8052/health/"

# Capturer les paquets sur un service
docker run --rm --network host --cap-add NET_ADMIN nicolaka/netshoot \
  tcpdump -i any -n host <IP_SERVICE> -c 50

# Voir les connexions actives vers un container
docker exec autoflow_traefik ss -tnp | head -20

# Latence interne
docker compose exec event_engine ping -c 5 awx_web
```
