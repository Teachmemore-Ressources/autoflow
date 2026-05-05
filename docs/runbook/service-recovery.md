---
title: Récupération des services
---

# Récupération des services

Procédures de récupération service par service, en cas de crash, corruption ou dysfonctionnement.

---

## AWX (awx_web + awx_task + awx_rsyslog)

### Symptômes courants

- HTTP 502 / 503 sur `https://awx.<DOMAIN>`
- Containers en `Restarting` ou `Exited`
- Jobs bloqués en "pending" indéfiniment

### Redémarrage standard

```bash
docker compose restart awx_web awx_task awx_rsyslog
# Attendre 30s puis vérifier
sleep 30 && docker compose ps | grep awx
```

### Redémarrage complet avec recréation

```bash
docker compose stop awx_web awx_task awx_rsyslog
docker compose up -d awx_web awx_task awx_rsyslog
```

### Si la base de données AWX est corrompue

```bash
# Arrêter AWX
docker compose stop awx_web awx_task

# Restaurer depuis la dernière sauvegarde Restic
# (voir docs/operations/backup-restore.md)
restic -r $BACKUP_REPOSITORY restore latest --include="/backup/awxdb" --target /tmp/restore

# Restaurer le dump PostgreSQL
docker compose exec awx_postgres psql -U awx < /tmp/restore/awxdb/awx-db-YYYYMMDD.sql

# Redémarrer
docker compose start awx_web awx_task
```

### Jobs bloqués en pending

```bash
# Voir les jobs pending
curl -u admin:<PASS> "https://awx.<DOMAIN>/api/v2/jobs/?status=pending" | python3 -m json.tool

# Annuler un job bloqué
curl -X POST -u admin:<PASS> "https://awx.<DOMAIN>/api/v2/jobs/<ID>/cancel/"

# Si le problème persiste — vérifier l'instance group
curl -u admin:<PASS> "https://awx.<DOMAIN>/api/v2/instance_groups/" | python3 -m json.tool
```

---

## Gitea (gitea + gitea_runner)

### Redémarrage standard

```bash
docker compose restart gitea
# Attendre que Gitea soit prêt (jusqu'à 30s)
until curl -sf https://git.<DOMAIN>/api/v1/repos/search?limit=1 > /dev/null; do
  sleep 3; echo "Waiting for Gitea..."
done
echo "Gitea ready"
```

### Runner déconnecté

```bash
# Vérifier la connexion du runner
docker logs autoflow_gitea_runner --tail=20

# Recréer le runner (il se re-enregistre automatiquement)
docker compose up -d --force-recreate gitea_runner
```

### Dépôt corrompu

```bash
# Via l'admin Gitea : Admin Panel → Git Repositories → <repo> → Git Hooks → Run Git FSck
# Ou via CLI :
docker compose exec gitea gitea admin git fsck

# Si irréparable, restaurer depuis Restic
# Voir docs/operations/backup-restore.md
```

---

## Event Engine

### Redémarrage

```bash
docker compose restart event_engine

# Vérifier qu'il se connecte à AWX
docker logs autoflow_event_engine --tail=20 | grep -i "awx\|error\|ready"
```

### Token AWX expiré ou révoqué

```bash
# 1. Créer un nouveau token AWX
curl -X POST -u admin:<PASS> "https://awx.<DOMAIN>/api/v2/tokens/" \
  -H "Content-Type: application/json" \
  -d '{"description": "Event Engine", "scope": "write"}'

# 2. Mettre à jour .env
# AWX_TOKEN=<nouveau_token>

# 3. Redémarrer
docker compose up -d --force-recreate event_engine
```

---

## Traefik

!!! warning "Service critique"
    Traefik est le point d'entrée unique. Son arrêt rend tous les services inaccessibles depuis l'extérieur.

### Redémarrage

```bash
docker compose restart traefik
# Vérifier les routes
sleep 5 && docker logs autoflow_traefik --tail=20
```

### Certificat Let's Encrypt expiré ou corrompu

```bash
# Supprimer le fichier ACME pour forcer le renouvellement
# (SEULEMENT si vous utilisez Let's Encrypt, pas la PKI interne)
docker compose stop traefik
rm traefik/acme.json
docker compose start traefik
```

### Règles de routing cassées

```bash
# Vérifier la configuration actuelle
docker exec autoflow_traefik traefik healthcheck

# Valider les fichiers de config
docker run --rm -v $(pwd)/traefik:/etc/traefik traefik:latest \
  traefik --configfile=/etc/traefik/traefik.yml --dryRun 2>&1 | head -50
```

---

## PostgreSQL (awx_postgres + pki_postgres)

### Vérification santé

```bash
# AWX Postgres
docker compose exec awx_postgres pg_isready -U awx
docker compose exec awx_postgres psql -U awx -c "SELECT version();"

# PKI Postgres
docker compose exec pki_postgres pg_isready -U pki
```

### Redémarrage

```bash
docker compose restart awx_postgres
# Attendre que Postgres soit prêt
until docker compose exec awx_postgres pg_isready -U awx > /dev/null 2>&1; do
  sleep 2; echo "Waiting for PostgreSQL..."
done
echo "PostgreSQL ready"
docker compose restart awx_web awx_task
```

### Espace disque insuffisant pour WAL

```bash
# Vérifier la taille des WAL
du -sh /var/lib/docker/volumes/autoflow_awx_postgres_data/

# Connexion d'urgence pour checkpoint
docker compose exec awx_postgres psql -U awx -c "CHECKPOINT;"
docker compose exec awx_postgres psql -U awx -c "SELECT pg_walfile_name(pg_current_wal_lsn());"
```

---

## Prometheus & Grafana

### Grafana inaccessible

```bash
docker compose restart grafana
sleep 10
curl -sf http://localhost:3000/api/health
```

### Prometheus ne scrape plus

```bash
# Vérifier les targets
curl -sf http://localhost:9090/api/v1/targets | python3 -m json.tool | grep '"health"'

# Vérifier la config
docker compose exec prometheus promtool check config /etc/prometheus/prometheus.yml

# Redémarrer
docker compose restart prometheus
```

### Loki — partition pleine

```bash
# Vérifier la taille
du -sh /var/lib/docker/volumes/autoflow_loki_data/

# Compacter les chunks (réduit l'espace)
curl -X POST http://localhost:3100/loki/api/v1/admin/compact

# Si toujours critique : purger les logs anciens via règles de rétention
# Éditer loki/loki-config.yml : limits_config.retention_period: 30d
docker compose restart loki
```

---

## Security Scanner

### Scan bloqué ou plus de rapports

```bash
# Vérifier le schedule
docker logs autoflow_security_scanner --tail=50

# Lancer un scan manuel
curl -X POST https://api.<DOMAIN>/security/scan/start \
  -H "Authorization: Bearer $SECURITY_ADMIN_TOKEN"

# Vérifier la base CVE (si outdated)
docker compose exec security_scanner trivy image --download-db-only
```

---

## Procédure de récupération complète (tous services)

En cas de redémarrage complet du serveur ou après maintenance :

```bash
cd /home/<user>/autoflow

# 1. Vérifier que Docker est démarré
sudo systemctl status docker

# 2. Démarrer les services de base d'abord
docker compose up -d awx_postgres pki_postgres
sleep 10

# 3. Démarrer le reste
docker compose up -d

# 4. Attendre que tout soit up (~60-90 secondes)
sleep 90

# 5. Vérifier l'état global
docker compose ps

# 6. Test de fumée
curl -sk https://awx.<DOMAIN>/health/ | python3 -m json.tool
curl -sk https://git.<DOMAIN>/api/v1/repos/search?limit=1 > /dev/null && echo "Gitea OK"
curl -sk https://monitoring.<DOMAIN>/api/health | python3 -m json.tool
```
