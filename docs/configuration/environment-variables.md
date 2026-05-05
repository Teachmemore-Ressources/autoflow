---
title: Variables d'environnement
---

# Variables d'environnement

Référence exhaustive de toutes les variables du fichier `.env`. Ce fichier est la **source unique de vérité** pour la configuration d'Autoflow.

**Légende :**
- 🔴 **Obligatoire** — la stack refuse de démarrer sans cette valeur
- 🟡 **Recommandé** — valeur par défaut acceptable en dev, à changer en prod
- 🟢 **Optionnel** — fonctionnalité désactivée si vide
- 🔑 **Secret** — ne jamais committer en clair, générer avec le wizard
- 🔗 **Dérivé** — calculé automatiquement depuis `DOMAIN`

---

## Système

| Variable | Type | Défaut | Statut | Description |
|---|---|---|---|---|
| `DEPLOY_USER` | string | — | 🔴 | Utilisateur Linux qui exécute le wizard et les scripts |
| `SUDO_PASSWORD` | string | — | 🔴 🔑 | Mot de passe sudo de `DEPLOY_USER` |
| `DOMAIN` | string | `localhost` | 🔴 | Domaine de base — tous les sous-domaines en découlent |

```bash
DEPLOY_USER=vagrant
SUDO_PASSWORD=<your_sudo_password>
DOMAIN=client.example.com
```

---

## Infrastructure / Réseau

| Variable | Type | Défaut | Statut | Description |
|---|---|---|---|---|
| `TRAEFIK_HTTP_PORT` | int | `80` | 🟢 | Port HTTP Traefik (entrypoint `web`) |
| `TRAEFIK_HTTPS_PORT` | int | `443` | 🟢 | Port HTTPS Traefik (entrypoint `websecure`) |
| `TRAEFIK_DASHBOARD_PORT` | int | `8080` | 🟢 | Port dashboard Traefik (désactivé par défaut) |
| `GITEA_SSH_PORT` | int | `2222` | 🟢 | Port SSH pour Git push/pull |

```bash
TRAEFIK_HTTP_PORT=80
TRAEFIK_HTTPS_PORT=443
GITEA_SSH_PORT=2222
```

---

## PostgreSQL (AWX)

| Variable | Type | Défaut | Statut | Description |
|---|---|---|---|---|
| `POSTGRES_DB` | string | `awx` | 🟡 | Nom de la base de données AWX |
| `POSTGRES_USER` | string | `awx` | 🟡 | Utilisateur PostgreSQL AWX |
| `POSTGRES_PASSWORD` | string | — | 🔴 🔑 | Mot de passe PostgreSQL AWX |

```bash
POSTGRES_DB=awx
POSTGRES_USER=awx
POSTGRES_PASSWORD=<generated_hex32>
```

!!! tip "Génération"
    ```bash
    python3 -c "import secrets; print(secrets.token_hex(32))"
    ```

---

## Redis

| Variable | Type | Défaut | Statut | Description |
|---|---|---|---|---|
| `REDIS_PASSWORD` | string | — | 🔴 🔑 | Mot de passe Redis |

```bash
REDIS_PASSWORD=<generated_hex32>
```

---

## AWX

| Variable | Type | Défaut | Statut | Description |
|---|---|---|---|---|
| `AWX_VERSION` | string | `24.6.1` | 🟡 | Version AWX (readonly en prod) |
| `AWX_ADMIN_USER` | string | `admin` | 🟡 | Nom d'utilisateur admin AWX |
| `AWX_ADMIN_PASSWORD` | string | — | 🔴 🔑 | Mot de passe admin AWX |
| `AWX_ADMIN_EMAIL` | email | `admin@autoflow.local` | 🟢 | Email admin AWX |
| `AWX_SECRET_KEY` | string | — | 🔴 🔑 | Clé Django (≥ 50 chars) — générer une seule fois |
| `AWX_ALLOWED_HOSTS` | string | `*` | 🟡 🔗 | Hosts Django autorisés (dérivé depuis DOMAIN) |
| `AWX_TOKEN` | string | — | 🟡 🔑 | Token API AWX pour l'Event Engine |
| `AWX_JOB_TEMPLATE_ID` | int | `1` | 🟡 | Template par défaut si aucune règle ne matche |
| `AWX_METRICS_INTERVAL` | int | `60` | 🟢 | Intervalle polling métriques AWX en secondes (0=désactivé) |
| `AWX_HTTP_PORT` | int | `80` | 🟢 | Port interne awx_web |

```bash
AWX_VERSION=24.6.1
AWX_ADMIN_USER=admin
AWX_ADMIN_PASSWORD=<strong_password>
AWX_ADMIN_EMAIL=admin@client.example.com
AWX_SECRET_KEY=<generated_hex64>
AWX_ALLOWED_HOSTS=awx.client.example.com,localhost,awxweb
AWX_TOKEN=<token_from_awx_ui>
AWX_JOB_TEMPLATE_ID=1
AWX_METRICS_INTERVAL=60
```

!!! danger "AWX_SECRET_KEY"
    Cette clé chiffre les sessions Django et les credentials AWX. **Ne jamais la changer après le premier démarrage** — cela invaliderait toutes les sessions et credentials existants.

!!! info "AWX_ALLOWED_HOSTS"
    Inclure toujours `localhost` et `awxweb` (nom interne Docker du service) en plus du sous-domaine public. Le wizard dérive la valeur correcte depuis `DOMAIN`.

---

## Autoflow API

| Variable | Type | Défaut | Statut | Description |
|---|---|---|---|---|
| `API_SECRET_KEY` | string | — | 🔴 🔑 | Clé maître de l'API |
| `API_USERNAME` | string | `admin` | 🟡 | Username pour `POST /auth/token` |
| `API_PASSWORD` | string | — | 🟢 🔑 | Password pour `POST /auth/token` (défaut = `API_SECRET_KEY`) |
| `API_PORT` | int | `8000` | 🟢 | Port interne de l'API |
| `CORS_ORIGINS` | string | — | 🟡 🔗 | Origins CORS autorisées (dérivé depuis DOMAIN) |
| `RATE_LIMIT` | string | `100/minute` | 🟡 | Limite de requêtes par IP (format slowapi) |
| `JWT_SECRET_KEY` | string | — | 🟡 🔑 | Clé de signature JWT (défaut = `API_SECRET_KEY`) |
| `JWT_EXPIRE_MINUTES` | int | `60` | 🟡 | Durée de vie des tokens JWT en minutes |
| `LOG_LEVEL` | enum | `info` | 🟢 | Verbosité des logs (debug/info/warning/error) |

```bash
API_SECRET_KEY=<generated_hex32>
API_USERNAME=admin
API_PASSWORD=          # vide = utilise API_SECRET_KEY
CORS_ORIGINS=https://awx.client.example.com,https://api.client.example.com
RATE_LIMIT=100/minute
JWT_SECRET_KEY=<generated_hex32>
JWT_EXPIRE_MINUTES=60
LOG_LEVEL=info
```

!!! note "CORS_ORIGINS"
    En production, restricter à vos domaines exacts. En développement, `*` est acceptable.

---

## Monitoring

| Variable | Type | Défaut | Statut | Description |
|---|---|---|---|---|
| `GRAFANA_ADMIN_USER` | string | `admin` | 🟡 | Username admin Grafana |
| `GRAFANA_ADMIN_PASSWORD` | string | — | 🔴 🔑 | Mot de passe admin Grafana |
| `PROMETHEUS_PORT` | int | `9090` | 🟢 | Port interne Prometheus |
| `PROMETHEUS_RETENTION` | string | `15d` | 🟡 | Rétention TSDB Prometheus |
| `GRAFANA_PORT` | int | `3000` | 🟢 | Port interne Grafana |
| `ALERTMANAGER_PORT` | int | `9093` | 🟢 | Port interne Alertmanager |
| `MONITORING_ADMIN_USER` | string | `admin` | 🟡 | Username BasicAuth Prometheus/Alertmanager |
| `MONITORING_ADMIN_PASSWORD` | string | — | 🔴 🔑 | Password BasicAuth (auto-haché dans traefik/dynamic/) |
| `LOKI_RETENTION` | string | `720h` | 🟡 | Rétention logs Loki |

```bash
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=<strong_password>
PROMETHEUS_RETENTION=15d
MONITORING_ADMIN_USER=admin
MONITORING_ADMIN_PASSWORD=<generated_urlsafe32>
LOKI_RETENTION=720h   # 30 jours
```

!!! tip "LOKI_RETENTION"
    Exemples courants :
    - `720h` = 30 jours (défaut)
    - `2160h` = 90 jours
    - `8760h` = 1 an
    Adapter selon l'espace disque disponible.

---

## MinIO

| Variable | Type | Défaut | Statut | Description |
|---|---|---|---|---|
| `MINIO_ROOT_USER` | string | `minioadmin` | 🟡 | Username root MinIO |
| `MINIO_ROOT_PASSWORD` | string | — | 🔴 🔑 | Mot de passe root MinIO (min. 8 chars) |
| `LOKI_S3_ACCESS_KEY` | string | `loki` | 🟡 | Identifiant compte MinIO dédié Loki |
| `LOKI_S3_SECRET_KEY` | string | — | 🔴 🔑 | Mot de passe compte MinIO dédié Loki |

```bash
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=<generated_hex32>
LOKI_S3_ACCESS_KEY=loki
LOKI_S3_SECRET_KEY=<generated_hex32>
```

!!! warning "Compte Loki"
    Le compte `loki` est créé automatiquement par `minio_init` au premier démarrage. Si vous changez `LOKI_S3_SECRET_KEY` après le démarrage, vous devez le mettre à jour manuellement via `mc admin user setpassword`.

---

## Event Engine

| Variable | Type | Défaut | Statut | Description |
|---|---|---|---|---|
| `EVENT_ENGINE_PORT` | int | `8001` | 🟢 | Port interne Event Engine |
| `EVENT_ENGINE_ADMIN_TOKEN` | string | — | 🔴 🔑 | Token Bearer pour `/admin/*` |
| `DEDUP_TTL` | int | `60` | 🟡 | Fenêtre de déduplication en secondes (0=désactivé) |
| `GITHUB_WEBHOOK_SECRET` | string | — | 🟡 🔑 | Secret HMAC pour valider les webhooks GitHub |
| `GITEA_WEBHOOK_SECRET` | string | — | 🟡 🔑 | Secret HMAC pour valider les webhooks Gitea |
| `NOTIFICATION_WEBHOOK_URL` | url | — | 🟢 | Webhook générique appelé à la fin des jobs |
| `NOTIFICATION_SLACK_WEBHOOK` | url | — | 🟢 | Slack incoming webhook |
| `JOB_WATCHER_INTERVAL` | int | `15` | 🟡 | Intervalle polling statut des jobs AWX (secondes) |

```bash
EVENT_ENGINE_ADMIN_TOKEN=<generated_urlsafe32>
DEDUP_TTL=60
GITHUB_WEBHOOK_SECRET=<generated_hex32>
GITEA_WEBHOOK_SECRET=<generated_hex32>
NOTIFICATION_SLACK_WEBHOOK=https://hooks.slack.com/services/...
JOB_WATCHER_INTERVAL=15
```

---

## Gitea

| Variable | Type | Défaut | Statut | Description |
|---|---|---|---|---|
| `GITEA_HTTP_PORT` | int | `3001` | 🟢 | Port interne Gitea |
| `GITEA_SSH_PORT` | int | `2222` | 🟢 | Port SSH exposé sur l'hôte |
| `GITEA_DOMAIN` | string | — | 🔗 | Domaine Gitea (dérivé depuis DOMAIN) |
| `GITEA_ROOT_URL` | url | — | 🔗 | URL publique Gitea (dérivé depuis DOMAIN) |
| `GITEA_ADMIN_USER` | string | `admin-gitea` | 🟡 | Username admin Gitea |
| `GITEA_ADMIN_PASSWORD` | string | — | 🔴 🔑 | Mot de passe admin Gitea |
| `GITEA_ADMIN_EMAIL` | email | `admin@localhost` | 🟢 | Email admin Gitea |
| `GITEA_SECRET_KEY` | string | — | 🔴 🔑 | Clé secrète Gitea — générer une seule fois |
| `GITEA_INTERNAL_TOKEN` | string | — | 🔴 🔑 | Token API interne Gitea |
| `GITEA_METRICS_TOKEN` | string | — | 🟡 🔑 | Token Prometheus pour scraper `/metrics` |
| `GITEA_WEBHOOK_SECRET` | string | — | 🟡 🔑 | Secret HMAC webhooks Gitea → Event Engine |
| `GITEA_DB_NAME` | string | `gitea` | 🟢 | Nom base PostgreSQL Gitea |
| `GITEA_DB_USER` | string | `gitea` | 🟢 | Username PostgreSQL Gitea |
| `GITEA_DB_PASSWORD` | string | — | 🔴 🔑 | Mot de passe PostgreSQL Gitea |
| `GITEA_LOG_LEVEL` | enum | `Warn` | 🟢 | Verbosité Gitea (Trace/Debug/Info/Warn/Error) |
| `GITEA_REGISTRY_TOKEN` | string | — | 🟡 🔑 | Token accès registry pour AWX |
| `GITEA_REGISTRY_OWNER_LIMIT` | int | `-1` | 🟢 | Limite stockage owner registry (-1=illimité) |
| `GITEA_REGISTRY_IMAGE_LIMIT` | int | `-1` | 🟢 | Limite stockage image registry (-1=illimité) |
| `GITEA_RUNNER_TOKEN` | string | — | 🟡 🔑 | Token d'enregistrement act_runner (généré par `make gitea-init-runner`) |
| `GITEA_USER` | string | `admin-gitea` | 🟡 | Owner des images EE dans le registry |

```bash
GITEA_DOMAIN=client.example.com
GITEA_ROOT_URL=https://git.client.example.com
GITEA_ADMIN_USER=admin-gitea
GITEA_ADMIN_PASSWORD=<strong_password>
GITEA_SECRET_KEY=<generated_hex64>
GITEA_INTERNAL_TOKEN=<generated_hex64>
GITEA_DB_PASSWORD=<generated_hex32>
GITEA_RUNNER_TOKEN=<generated_by_make_gitea_init_runner>
```

---

## PKI

| Variable | Type | Défaut | Statut | Description |
|---|---|---|---|---|
| `PKI_PORT` | int | `8004` | 🟢 | Port interne PKI |
| `PKI_ADMIN_USER` | string | `admin` | 🟡 | Username admin PKI |
| `PKI_ADMIN_PASSWORD` | string | — | 🔴 🔑 | Mot de passe admin PKI |
| `PKI_JWT_SECRET` | string | — | 🔴 🔑 | Clé de signature JWT PKI — générer une seule fois |
| `PKI_BASE_URL` | url | — | 🔗 | URL publique PKI (dérivé depuis DOMAIN) |
| `PKI_KEY_PASSPHRASE` | string | — | 🟢 🔑 | Passphrase chiffrement clés privées (vide=désactivé) |
| `PKI_ALLOWED_ORIGINS` | string | — | 🟢 | CORS origins supplémentaires pour PKI |

```bash
PKI_ADMIN_USER=admin
PKI_ADMIN_PASSWORD=<strong_password>
PKI_JWT_SECRET=<generated_hex64>
PKI_BASE_URL=https://pki.client.example.com
```

---

## Execution Environments

| Variable | Type | Défaut | Statut | Description |
|---|---|---|---|---|
| `EE_BUILDER_PORT` | int | `8003` | 🟢 | Port interne EE Builder |
| `EE_DEFAULT_VERSION` | string | `latest` | 🟡 | Tag Docker appliqué aux images EE buildées |
| `EE_DNS_SERVER` | string | — | 🟢 | DNS pour les conteneurs EE (vide = Docker auto-gère) |
| `BUILD_PYCMD` | string | `/usr/bin/python3.12` | 🟡 | Chemin Python pour ansible-builder |
| `DOCKER_GID` | int | `999` | 🟡 | GID du socket Docker (`stat -c '%g' /var/run/docker.sock`) |

```bash
EE_DEFAULT_VERSION=latest
EE_DNS_SERVER=          # LAISSER VIDE — Docker gère automatiquement
BUILD_PYCMD=/usr/bin/python3.12
DOCKER_GID=999          # Vérifier: stat -c '%g' /var/run/docker.sock
```

!!! danger "EE_DNS_SERVER"
    Laisser **vide** sauf cas exceptionnel. Si défini, Docker remplace ENTIÈREMENT son resolv.conf par ce seul serveur — si le serveur est inaccessible depuis le bridge, tous les jobs AWX échouent.
    Utiliser le bouton "Detect DNS" du wizard pour tester et choisir.

---

## Sécurité — Scanner

| Variable | Type | Défaut | Statut | Description |
|---|---|---|---|---|
| `SECURITY_SCANNER_PORT` | int | `8002` | 🟢 | Port interne Security Scanner |
| `SCAN_INTERVAL` | int | `21600` | 🟡 | Intervalle scan CVE en secondes (21600=6h) |
| `VERSION_CHECK_INTERVAL` | int | `3600` | 🟡 | Intervalle vérification versions en secondes (1h) |
| `DOCKER_GID` | int | `999` | 🟡 | GID socket Docker (partagé avec EE) |
| `TRIVY_VERSION` | string | `0.63.0` | 🟡 | Version Trivy (pinned) |
| `TRIVY_TIMEOUT` | int | `300` | 🟡 | Timeout scan par image en secondes |
| `GITHUB_TOKEN` | string | — | 🟢 🔑 | Token GitHub (augmente les rate limits API à 5000/h) |
| `DOCKERHUB_USER` | string | — | 🟢 | Username DockerHub (limite tag lookups) |
| `DOCKERHUB_PASSWORD` | string | — | 🟢 🔑 | Password DockerHub |
| `COMPLIANCE_ADMIN_TOKEN` | string | — | 🔴 🔑 | Token Bearer pour les endpoints compliance |

```bash
SCAN_INTERVAL=21600
TRIVY_VERSION=0.63.0
TRIVY_TIMEOUT=300
COMPLIANCE_ADMIN_TOKEN=<generated_hex32>
```

---

## Backup & Disaster Recovery

| Variable | Type | Défaut | Statut | Description |
|---|---|---|---|---|
| `BACKUP_RESTIC_PASSWORD` | string | — | 🔴 🔑 | Mot de passe chiffrement repository Restic |
| `BACKUP_ENCRYPTION_KEY` | string | — | 🟡 🔑 | Clé AES-256 pour chiffrement des backups DB |
| `BACKUP_BACKEND` | enum | `local` | 🔴 | Backend stockage (local/sftp/s3/b2) |
| `BACKUP_LOCAL_PATH` | string | `/var/backups/autoflow/restic` | 🟡 | Chemin local ou URL SFTP |
| `BACKUP_S3_ENDPOINT` | url | — | 🟢 | Endpoint S3-compatible (vide=AWS S3) |
| `BACKUP_S3_BUCKET` | string | — | 🟡 | Nom du bucket S3/B2 |
| `BACKUP_S3_ACCESS_KEY` | string | — | 🟡 🔑 | Access key S3 / B2 Account ID |
| `BACKUP_S3_SECRET_KEY` | string | — | 🟡 🔑 | Secret key S3 / B2 Application Key |
| `BACKUP_RETENTION_DAILY` | int | `7` | 🟡 | Snapshots journaliers à conserver |
| `BACKUP_RETENTION_WEEKLY` | int | `4` | 🟡 | Snapshots hebdomadaires à conserver |
| `BACKUP_RETENTION_MONTHLY` | int | `12` | 🟡 | Snapshots mensuels à conserver |
| `BACKUP_RETENTION_YEARLY` | int | `3` | 🟡 | Snapshots annuels à conserver |
| `BACKUP_CRON` | cron | `"0 2 * * *"` | 🟡 | Schedule cron backup automatique |
| `BACKUP_RTO_HOURS` | int | `4` | 🟢 | Recovery Time Objective (documentation SLA) |
| `BACKUP_RPO_HOURS` | int | `24` | 🟢 | Recovery Point Objective (documentation SLA) |

```bash
BACKUP_RESTIC_PASSWORD=<generated_urlsafe32>
BACKUP_BACKEND=sftp
BACKUP_LOCAL_PATH=sftp://backupuser@backup-server:22/backups/autoflow
BACKUP_RETENTION_DAILY=7
BACKUP_RETENTION_WEEKLY=4
BACKUP_RETENTION_MONTHLY=12
BACKUP_CRON="0 2 * * *"   # Guillemets obligatoires (caractères spéciaux)
```

!!! warning "BACKUP_CRON — guillemets obligatoires"
    La valeur cron **doit être entre guillemets** dans `.env` :
    ```bash
    BACKUP_CRON="0 2 * * *"   # ✅ Correct
    BACKUP_CRON=0 2 * * *     # ❌ Erreur bash : "2: command not found"
    ```
    Le wizard gère cela automatiquement. En éditant `.env` manuellement, ne pas oublier les guillemets.

---

## Conformité & Audit

| Variable | Type | Défaut | Statut | Description |
|---|---|---|---|---|
| `COMPLIANCE_ADMIN_TOKEN` | string | — | 🔴 🔑 | Token Bearer pour POST /compliance/report/generate |

```bash
COMPLIANCE_ADMIN_TOKEN=<generated_hex32>
```

---

## OpenTelemetry

| Variable | Type | Défaut | Statut | Description |
|---|---|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | url | `http://otel-collector:4317` | 🟢 | Endpoint OTLP (tous les services FastAPI envoient ici) |

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
```

---

## Gitea Actions Runner

| Variable | Type | Défaut | Statut | Description |
|---|---|---|---|---|
| `GITEA_RUNNER_TOKEN` | string | — | 🟡 | Token enregistrement runner (généré par `make gitea-init-runner`) |

```bash
GITEA_RUNNER_TOKEN=<generated_by_script>
```

!!! note
    Cette valeur est renseignée automatiquement par `make gitea-init-runner`. Ne pas la modifier manuellement.

---

## Variables internes / générées

Ces variables sont générées par le wizard ou les scripts d'initialisation. Ne pas les modifier manuellement.

| Variable | Source | Description |
|---|---|---|
| `_ee_registry_info` | Wizard | Informations registry EE (JSON interne) |
