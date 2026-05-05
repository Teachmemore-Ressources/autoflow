---
title: Backup & Restore
---

# Backup & Restore

Procédures de sauvegarde et restauration testées. Lire aussi la [configuration backup](../configuration/backup.md).

---

## Backup manuel

```bash
make backup
```

Les backups sont horodatés dans `backups/` :
```
backups/
├── 2024-05-01_02-00-00/
│   ├── awx_postgres.sql.gz
│   ├── gitea_postgres.sql.gz
│   └── manifest.json
└── 2024-04-30_02-00-00/
```

---

## Vérifier les snapshots Restic

```bash
# Lister tous les snapshots
RESTIC_PASSWORD="$BACKUP_RESTIC_PASSWORD" \
RESTIC_REPOSITORY="$BACKUP_LOCAL_PATH" \
restic snapshots

# Statistiques du repository
RESTIC_PASSWORD="$BACKUP_RESTIC_PASSWORD" \
RESTIC_REPOSITORY="$BACKUP_LOCAL_PATH" \
restic stats
```

---

## Procédure de restore

!!! danger "Arrêter les services avant de restaurer"
    Un restore sur une stack en cours de fonctionnement peut corrompre les données.

```bash
# 1. Arrêter la stack
make stop

# 2. Lister les backups disponibles
ls -lt backups/

# 3. Restaurer
make restore BACKUP=./backups/2024-05-01_02-00-00

# 4. Redémarrer
make start

# 5. Vérifier
make status
```

---

## Tester les backups régulièrement

!!! tip "Règle d'or"
    Un backup non testé est un backup inutile. Tester la restauration en environnement de test **au moins une fois par mois**.

```bash
# Test rapide : vérifier l'intégrité du repository Restic
RESTIC_PASSWORD="$BACKUP_RESTIC_PASSWORD" \
RESTIC_REPOSITORY="$BACKUP_LOCAL_PATH" \
restic check

# Test complet : vérifier les données
restic check --read-data
```

---

## Automatiser le backup

Le cron est géré par le wizard (Section Backup & DR → "Install cron") ou manuellement :

```bash
# Vérifier le cron actif
crontab -l | grep backup

# Ajouter manuellement
crontab -e
# Ajouter : 0 2 * * * /home/vagrant/autoflow/scripts/backup.sh
```
