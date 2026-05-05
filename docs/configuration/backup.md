---
title: Backup & Disaster Recovery
---

# Backup & Disaster Recovery

Autoflow utilise **Restic** pour des backups chiffrés, versionnés et déduplicatés. La politique GFS (Grandfather-Father-Son) conserve des snapshots journaliers, hebdomadaires, mensuels et annuels.

---

## Ce qui est sauvegardé

| Donnée | Méthode | Criticité |
|---|---|---|
| PostgreSQL AWX (jobs, config, credentials) | `pg_dump` + Restic | 🔴 Critique |
| PostgreSQL Gitea (repos metadata) | `pg_dump` + Restic | 🔴 Critique |
| Redis (queue/cache) | `BGSAVE` + Restic | 🟡 Important |
| Volumes AWX projects | Restic direct | 🟡 Important |
| Fichier `.env.enc` | Git + Restic | 🔴 Critique |
| Clé Age | Hors serveur uniquement | 🔴 Critique |

!!! danger "Ce qui N'est PAS sauvegardé automatiquement"
    - La **clé Age** (`~/.config/sops/age/keys.txt`) — à sauvegarder manuellement hors du serveur
    - Le **mot de passe Restic** (`BACKUP_RESTIC_PASSWORD`) — stocker dans un gestionnaire de mots de passe séparé

---

## Backends supportés

=== "Local (dev/lab uniquement)"

    ```bash
    BACKUP_BACKEND=local
    BACKUP_LOCAL_PATH=/var/backups/autoflow/restic
    ```

    ```bash
    # Créer le répertoire
    sudo mkdir -p /var/backups/autoflow/restic
    sudo chown $USER:$USER /var/backups/autoflow/restic
    ```

    !!! warning "⚠ Non recommandé en production"
        Un backup sur le même serveur ne protège pas contre une panne matérielle, incendie ou ransomware. Utiliser SFTP ou S3 pour un vrai DR.

=== "SFTP (recommandé)"

    ```bash
    BACKUP_BACKEND=sftp
    BACKUP_LOCAL_PATH=sftp://backupuser@backup-server.example.com:22/backups/autoflow
    ```

    Le serveur distant doit avoir une clé SSH autorisée. Tester la connexion :
    ```bash
    ssh backupuser@backup-server.example.com 'ls /backups/autoflow' 
    ```

=== "S3 / Compatible"

    ```bash
    BACKUP_BACKEND=s3
    BACKUP_S3_BUCKET=autoflow-backup-prod
    BACKUP_S3_ENDPOINT=          # vide = AWS S3
    # Pour MinIO, Wasabi, Scaleway, OVH :
    BACKUP_S3_ENDPOINT=https://s3.wasabisys.com
    BACKUP_S3_ACCESS_KEY=<access_key>
    BACKUP_S3_SECRET_KEY=<secret_key>
    ```

=== "Backblaze B2"

    ```bash
    BACKUP_BACKEND=b2
    BACKUP_S3_BUCKET=autoflow-backup
    BACKUP_S3_ACCESS_KEY=<b2_account_id>
    BACKUP_S3_SECRET_KEY=<b2_application_key>
    ```

---

## Politique de rétention GFS

| Variable | Défaut | Snapshots conservés |
|---|---|---|
| `BACKUP_RETENTION_DAILY` | `7` | 7 derniers jours |
| `BACKUP_RETENTION_WEEKLY` | `4` | 4 dernières semaines |
| `BACKUP_RETENTION_MONTHLY` | `12` | 12 derniers mois |
| `BACKUP_RETENTION_YEARLY` | `3` | 3 dernières années |

Exemple de calendrier résultant :

```
Aujourd'hui (J)       → snapshot journalier
J-1, J-2, ..., J-6   → 6 snapshots journaliers
Dernier dim de chaque semaine × 4  → hebdomadaires
Dernier jour de chaque mois × 12  → mensuels
31 décembre × 3 ans               → annuels
```

---

## Schedule cron

```bash
# Dans .env — guillemets OBLIGATOIRES
BACKUP_CRON="0 2 * * *"    # 2h00 UTC tous les jours
```

!!! danger "Guillemets obligatoires"
    Sans guillemets, bash interprète `0 2 * * *` comme : assigner `0` à la variable puis exécuter `2` comme commande → `2: command not found` (exit 127).

    ```bash
    BACKUP_CRON="0 2 * * *"   # ✅
    BACKUP_CRON=0 2 * * *     # ❌ erreur bash
    ```

---

## Commandes

### Backup manuel

```bash
make backup
```

Sortie attendue :
```
[backup] Starting PostgreSQL dump...
[backup] Compressing and encrypting...
[backup] Snapshot 8a3f2b1 saved.
[backup] Applying retention policy (keep: 7 daily, 4 weekly, 12 monthly, 3 yearly)
[backup] Removed 2 old snapshots.
[backup] Done. Duration: 45s
```

### Lister les snapshots

```bash
# Via le script
bash scripts/backup.sh list

# Directement avec Restic (nécessite BACKUP_RESTIC_PASSWORD)
export RESTIC_PASSWORD="$BACKUP_RESTIC_PASSWORD"
export RESTIC_REPOSITORY="$BACKUP_LOCAL_PATH"
restic snapshots
```

### Restore

```bash
# Lister les backups disponibles
ls -lt backups/

# Restaurer depuis un snapshot
make restore BACKUP=./backups/2024-05-01_02-00-00
```

!!! warning "Restore : arrêter les services d'abord"
    ```bash
    make stop
    make restore BACKUP=./backups/<timestamp>
    make start
    ```

### Vérifier l'intégrité

```bash
# Vérifier les métadonnées du repository
restic check

# Vérifier les données (plus lent, vérifie les blobs)
restic check --read-data
```

---

## RTO / RPO

Ces objectifs documentent votre SLA. Ils ne sont pas techniques — ce sont des engagements à respecter.

| Variable | Défaut | Description |
|---|---|---|
| `BACKUP_RTO_HOURS` | `4` | Recovery Time Objective : temps max avant retour en service |
| `BACKUP_RPO_HOURS` | `24` | Recovery Point Objective : perte de données max acceptable |

Un `BACKUP_CRON="0 2 * * *"` (journalier) correspond à un RPO de 24h.  
Pour un RPO de 1h, configurer : `BACKUP_CRON="0 * * * *"`.

---

## Chiffrement

Restic chiffre toutes les données avec **AES-256-CTR + Poly1305-AES** (chiffrement authentifié). La clé de chiffrement est dérivée de `BACKUP_RESTIC_PASSWORD` via scrypt.

!!! danger "Perte du mot de passe = perte des données"
    Sans `BACKUP_RESTIC_PASSWORD`, il est **impossible** de déchiffrer les backups Restic. Stocker ce mot de passe :
    - Dans un gestionnaire de mots de passe (Bitwarden, 1Password)
    - Dans un coffre-fort physique
    - **Jamais uniquement sur le serveur sauvegardé**
