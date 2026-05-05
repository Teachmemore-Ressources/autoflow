---
title: Reprise après désastre
---

# Reprise après désastre (Disaster Recovery)

Ce guide couvre le scénario le plus sévère : **perte totale du serveur Autoflow**. Il décrit comment reconstruire la plateforme complète depuis zéro en utilisant les sauvegardes Restic.

---

## Prérequis

- Accès aux sauvegardes Restic (S3, SFTP ou disque externe)
- `BACKUP_REPOSITORY` et `BACKUP_PASSWORD` disponibles (stockés séparément des backups)
- Nouveau serveur avec les mêmes prérequis que l'installation initiale

!!! danger "Conservation des secrets"
    Les credentials de backup (`BACKUP_REPOSITORY`, `BACKUP_PASSWORD`) ne doivent **jamais** être stockés uniquement sur le serveur sauvegardé. Conservez-les dans un gestionnaire de mots de passe ou coffre-fort externe (Vault, Bitwarden, 1Password).

---

## RTO / RPO estimés

| Métrique | Valeur | Conditions |
|---|---|---|
| **RPO** (perte de données max) | 1h (cron toutes les heures) | Sauvegarde incrémentale |
| **RTO** (temps de restauration) | 2-4h | Serveur pré-provisionné |
| **RTO** (serveur from scratch) | 4-8h | Includes OS setup |

---

## Étape 1 — Provisionner le nouveau serveur

Suivre [les prérequis](../getting-started/prerequisites.md) pour préparer un nouveau serveur :

```bash
# Installer Docker
curl -fsSL https://get.docker.com | sudo bash
sudo usermod -aG docker $USER
newgrp docker

# Installer les outils nécessaires
sudo apt install -y git restic age sops

# Vérifier les versions
docker --version
restic version
age --version
```

---

## Étape 2 — Cloner le dépôt de configuration

```bash
git clone <URL_DU_REPO_AUTOFLOW> autoflow
cd autoflow
```

Si le dépôt de configuration est aussi perdu, reconstruire depuis le code source :

```bash
git clone https://github.com/<org>/autoflow.git
cd autoflow
```

---

## Étape 3 — Restaurer le fichier `.env`

### Option A — Le `.env.enc` est dans le dépôt (recommandé)

```bash
# Récupérer la clé SOPS depuis votre coffre-fort externe
export SOPS_AGE_KEY="<votre_clé_privée_age>"

# Déchiffrer
sops --decrypt .env.enc > .env

# Vérifier
head -5 .env
```

### Option B — Restaurer `.env` depuis la sauvegarde Restic

```bash
# Lister les snapshots disponibles
restic -r $BACKUP_REPOSITORY snapshots --password-command "echo $BACKUP_PASSWORD"

# Restaurer le dernier snapshot
restic -r $BACKUP_REPOSITORY restore latest \
  --target /tmp/dr-restore \
  --password-command "echo $BACKUP_PASSWORD"

# Copier le .env restauré
cp /tmp/dr-restore/backup/config/.env ./autoflow/.env
```

---

## Étape 4 — Créer les volumes critiques

Les volumes critiques sont `external: true` — ils doivent exister avant `docker compose up`.
Sur un nouveau serveur ils n'existent pas encore :

```bash
docker volume create autoflow_postgres_data
docker volume create autoflow_gitea_postgres_data
docker volume create autoflow_gitea_data
docker volume create autoflow_redis_data
docker volume create autoflow_pki_data
```

!!! tip
    Le Deploy Wizard fait cela automatiquement. En DR manuel, il faut le faire explicitement.

---

## Étape 5 — Restaurer les données

### Base de données AWX

```bash
# Identifier le snapshot le plus récent
restic -r $BACKUP_REPOSITORY snapshots | tail -5

# Restaurer les dumps PostgreSQL
restic -r $BACKUP_REPOSITORY restore latest \
  --include "/backup/awxdb" \
  --target /tmp/dr-restore

# Démarrer PostgreSQL seul (sans AWX)
cd autoflow
docker compose up -d awx_postgres
sleep 15

# Vérifier que PostgreSQL est prêt
docker compose exec awx_postgres pg_isready -U awx

# Identifier le dump le plus récent
ls -la /tmp/dr-restore/backup/awxdb/

# Restaurer
docker compose exec -T awx_postgres psql -U awx < /tmp/dr-restore/backup/awxdb/awx-db-YYYYMMDD-HHMMSS.sql
```

### Base de données PKI

```bash
restic -r $BACKUP_REPOSITORY restore latest \
  --include "/backup/pkidb" \
  --target /tmp/dr-restore

docker compose up -d pki_postgres
sleep 10

docker compose exec -T pki_postgres psql -U pki < /tmp/dr-restore/backup/pkidb/pki-db-YYYYMMDD-HHMMSS.sql
```

### Dépôts Gitea

```bash
restic -r $BACKUP_REPOSITORY restore latest \
  --include "/backup/gitea" \
  --target /tmp/dr-restore

# Copier les données Gitea vers le volume Docker (créé à l'étape 4)
docker run --rm \
  -v autoflow_gitea_data:/target \
  -v /tmp/dr-restore/backup/gitea:/source \
  alpine sh -c "cp -a /source/. /target/"
```

### Données Prometheus & Grafana (optionnel)

```bash
restic -r $BACKUP_REPOSITORY restore latest \
  --include "/backup/monitoring" \
  --target /tmp/dr-restore

# Prometheus
docker run --rm \
  -v autoflow_prometheus_data:/target \
  -v /tmp/dr-restore/backup/monitoring/prometheus:/source \
  alpine sh -c "cp -a /source/. /target/"

# Grafana
docker run --rm \
  -v autoflow_grafana_data:/target \
  -v /tmp/dr-restore/backup/monitoring/grafana:/source \
  alpine sh -c "cp -a /source/. /target/"
```

---

## Étape 6 — Démarrer la plateforme

```bash
cd autoflow

# Démarrage complet
docker compose up -d

# Monitorer le démarrage
watch -n 3 'docker compose ps'
```

Attendre ~2-3 minutes. AWX peut prendre jusqu'à 5 minutes pour initialiser sa DB au premier démarrage.

---

## Étape 7 — Vérifications post-restauration

### Tests fonctionnels

```bash
# AWX accessible
curl -sk https://awx.<DOMAIN>/api/v2/ping/ | python3 -m json.tool

# Gitea accessible
curl -sk https://git.<DOMAIN>/api/v1/repos/search?limit=1

# PKI accessible
curl -sk https://pki.<DOMAIN>/api/v1/health

# Event Engine
curl -sk https://api.<DOMAIN>/health
```

### Vérifications dans AWX

```bash
# Vérifier que les credentials sont présents
curl -u admin:<PASS> "https://awx.<DOMAIN>/api/v2/credentials/" | python3 -c \
  "import sys,json; [print(c['name']) for c in json.load(sys.stdin)['results']]"

# Vérifier les job templates
curl -u admin:<PASS> "https://awx.<DOMAIN>/api/v2/job_templates/" | python3 -c \
  "import sys,json; [print(t['name']) for t in json.load(sys.stdin)['results']]"

# Lancer un job de test
curl -X POST -u admin:<PASS> "https://awx.<DOMAIN>/api/v2/job_templates/<ID>/launch/"
```

### Vérifications Gitea

```bash
# Vérifier les dépôts
curl -sk https://git.<DOMAIN>/api/v1/repos/search | python3 -c \
  "import sys,json; [print(r['full_name']) for r in json.load(sys.stdin)['data']]"

# Vérifier les webhooks
# Dans Gitea UI : chaque repo → Settings → Webhooks
```

---

## Étape 8 — Reconfiguration post-restauration

### Régénérer les tokens si nécessaire

Si les tokens ont été révoqués dans le cadre d'un incident de sécurité :

```bash
# Nouveau token AWX pour Event Engine
curl -X POST -u admin:<PASS> "https://awx.<DOMAIN>/api/v2/tokens/" \
  -H "Content-Type: application/json" \
  -d '{"description": "Event Engine DR", "scope": "write"}'

# Mettre à jour .env
# AWX_TOKEN=<nouveau_token>
docker compose up -d --force-recreate event_engine
```

### Re-chiffrer le `.env`

```bash
# Une fois la restauration validée, re-chiffrer le .env
sops --encrypt .env > .env.enc
git add .env.enc
git commit -m "chore: re-encrypt env after DR"
git push
```

---

## Checklist de validation finale

- [ ] AWX accessible et opérationnel
- [ ] Tous les job templates présents
- [ ] Tous les credentials présents
- [ ] Gitea accessible avec tous les dépôts
- [ ] Webhooks Gitea → Event Engine fonctionnels
- [ ] PKI accessible et certificats valides
- [ ] Prometheus scrappe toutes les targets
- [ ] Grafana affiche les dashboards
- [ ] Alertmanager opérationnel
- [ ] Event Engine reçoit les webhooks
- [ ] Sauvegarde Restic reprend automatiquement
- [ ] `.env.enc` mis à jour dans le dépôt

---

## Prévention et préparation

### Test de restauration mensuel (recommandé)

```bash
# Sur un serveur de test, restaurer le dernier snapshot
restic -r $BACKUP_REPOSITORY restore latest --target /tmp/dr-test-$(date +%Y%m)

# Vérifier l'intégrité des fichiers restaurés
ls -la /tmp/dr-test-$(date +%Y%m)/backup/
du -sh /tmp/dr-test-$(date +%Y%m)/backup/*

# Nettoyer
rm -rf /tmp/dr-test-$(date +%Y%m)
```

### Documentation à maintenir à jour

- Clé SOPS privée → coffre-fort externe
- `BACKUP_REPOSITORY` URL → coffre-fort externe  
- `BACKUP_PASSWORD` → coffre-fort externe
- Ce runbook → dépôt Git (versionné avec la config)
