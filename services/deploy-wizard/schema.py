"""
Field definitions for the Autoflow Deploy Wizard.
Each field maps to one .env variable.
"""

SECTIONS = [
    {"id": "system",         "label": "Système",          "desc": "Utilisateur et mot de passe sudo pour les opérations privilégiées"},
    {"id": "infrastructure", "label": "Infrastructure",   "desc": "Domain, ports and network topology"},
    {"id": "postgresql",     "label": "PostgreSQL",       "desc": "Main database credentials"},
    {"id": "redis",          "label": "Redis",            "desc": "Cache / queue credentials"},
    {"id": "awx",            "label": "AWX",              "desc": "Automation platform configuration"},
    {"id": "api",            "label": "Autoflow API",     "desc": "REST API, JWT and rate limiting"},
    {"id": "monitoring",     "label": "Monitoring",       "desc": "Grafana, Prometheus and alerting"},
    {"id": "minio",          "label": "MinIO",            "desc": "S3-compatible object storage — backend de stockage Loki (logs)"},
    {"id": "event_engine",   "label": "Event Engine",     "desc": "Webhooks, notifications and job routing"},
    {"id": "gitea",          "label": "Gitea",            "desc": "Self-hosted Git service and registry"},
    {"id": "pki",            "label": "PKI",              "desc": "Internal certificate authority"},
    {"id": "advanced",       "label": "Advanced",         "desc": "Intervals, log levels and system tuning"},
    {"id": "backup",         "label": "Disaster Recovery","desc": "Restic-based encrypted backups: remote push, GFS retention, cron scheduling and smoke-test restore."},
    {"id": "compliance",     "label": "Compliance & Audit","desc": "On-demand CVE→framework mapping: NIST SP 800-53, CIS Controls v8, SOC2 TSC, ISO 27001:2022, PCI-DSS v4.0."},
]

# generate_type: hex32 | hex64 | urlsafe32
FIELDS = [
    # ── Système ───────────────────────────────────────────────────
    {
        "key": "DEPLOY_USER", "label": "Utilisateur système", "section": "system",
        "type": "text", "required": True,
        "default": "",
        "placeholder": "armel",
        "description": "Nom d'utilisateur Linux qui exécute le wizard et les commandes de déploiement.",
    },
    {
        "key": "SUDO_PASSWORD", "label": "Mot de passe sudo", "section": "system",
        "type": "password", "sensitive": True,
        "default": "",
        "placeholder": "••••••••",
        "description": "Mot de passe sudo de l'utilisateur ci-dessus — utilisé pour les opérations privilégiées (CA Docker, /etc/hosts, daemon restart). Jamais transmis à l'extérieur.",
    },

    # ── Infrastructure ────────────────────────────────────────────
    {
        "key": "DOMAIN", "label": "Domain", "section": "infrastructure",
        "type": "text", "required": True, "default": "localhost",
        "description": "Base domain for all services. Subdomains are derived automatically.",
        "placeholder": "autoflow.example.com",
        "derive_trigger": True,
    },
    {
        "key": "TRAEFIK_HTTP_PORT", "label": "HTTP Port", "section": "infrastructure",
        "type": "number", "default": "80", "description": "Traefik HTTP entrypoint port",
    },
    {
        "key": "TRAEFIK_HTTPS_PORT", "label": "HTTPS Port", "section": "infrastructure",
        "type": "number", "default": "443", "description": "Traefik HTTPS entrypoint port",
    },
    {
        "key": "GITEA_SSH_PORT", "label": "Gitea SSH Port", "section": "infrastructure",
        "type": "number", "default": "2222", "description": "TCP port exposed for Git-over-SSH",
    },

    # ── PostgreSQL ─────────────────────────────────────────────────
    {
        "key": "POSTGRES_DB", "label": "Database Name", "section": "postgresql",
        "type": "text", "default": "awx", "description": "AWX PostgreSQL database name",
    },
    {
        "key": "POSTGRES_USER", "label": "Username", "section": "postgresql",
        "type": "text", "default": "awx", "description": "AWX PostgreSQL username",
    },
    {
        "key": "POSTGRES_PASSWORD", "label": "Password", "section": "postgresql",
        "type": "password", "required": True, "auto_generate": True, "generate_type": "hex32",
        "description": "Strong password for the AWX database",
    },
    {
        "key": "BACKUP_ENCRYPTION_KEY", "label": "Backup Encryption Key", "section": "postgresql",
        "type": "password", "auto_generate": True, "generate_type": "hex32",
        "description": "AES-256 key to encrypt database backups. Leave empty to skip encryption.",
    },

    # ── Redis ──────────────────────────────────────────────────────
    {
        "key": "REDIS_PASSWORD", "label": "Password", "section": "redis",
        "type": "password", "required": True, "auto_generate": True, "generate_type": "hex32",
        "description": "Strong password for Redis",
    },

    # ── AWX ───────────────────────────────────────────────────────
    {
        "key": "AWX_VERSION", "label": "AWX Version", "section": "awx",
        "type": "text", "default": "24.6.1", "readonly": True,
        "description": "Pinned AWX version — change only if you know what you are doing",
    },
    {
        "key": "AWX_ADMIN_USER", "label": "Admin Username", "section": "awx",
        "type": "text", "default": "admin", "required": True,
        "description": "AWX web UI admin username",
    },
    {
        "key": "AWX_ADMIN_PASSWORD", "label": "Admin Password", "section": "awx",
        "type": "password", "required": True,
        "description": "AWX web UI admin password",
    },
    {
        "key": "AWX_ADMIN_EMAIL", "label": "Admin Email", "section": "awx",
        "type": "email", "default": "admin@autoflow.local",
        "description": "AWX admin email address",
    },
    {
        "key": "AWX_SECRET_KEY", "label": "Secret Key", "section": "awx",
        "type": "password", "required": True, "auto_generate": True, "generate_type": "hex64",
        "description": "Django secret key — generate once, never change after first start",
    },
    {
        "key": "AWX_TOKEN", "label": "API Token", "section": "awx",
        "type": "password",
        "description": "AWX API token (create in AWX UI after first start: User → Tokens → Add). Leave empty for initial setup.",
    },
    {
        "key": "AWX_JOB_TEMPLATE_ID", "label": "Default Job Template ID", "section": "awx",
        "type": "number", "default": "1",
        "description": "Default AWX job template triggered on incoming events",
    },

    # ── Autoflow API ───────────────────────────────────────────────
    {
        "key": "API_SECRET_KEY", "label": "Secret Key", "section": "api",
        "type": "password", "required": True, "auto_generate": True, "generate_type": "hex32",
        "description": "Master secret key for the Autoflow API",
    },
    {
        "key": "API_USERNAME", "label": "API Username", "section": "api",
        "type": "text", "default": "admin",
        "description": "Username for POST /auth/token",
    },
    {
        "key": "API_PASSWORD", "label": "API Password", "section": "api",
        "type": "password",
        "description": "Password for POST /auth/token. Defaults to API_SECRET_KEY if empty.",
    },
    {
        "key": "CORS_ORIGINS", "label": "CORS Origins", "section": "api",
        "type": "text", "wide": True,
        "description": "Comma-separated allowed origins. Auto-derived from Domain.",
        "placeholder": "https://awx.example.com,https://api.example.com",
        "derived": True,
    },
    {
        "key": "RATE_LIMIT", "label": "Rate Limit", "section": "api",
        "type": "text", "default": "100/minute",
        "description": "API rate limit per IP (slowapi format)",
    },
    {
        "key": "JWT_SECRET_KEY", "label": "JWT Secret Key", "section": "api",
        "type": "password", "auto_generate": True, "generate_type": "hex32",
        "description": "JWT signing secret. Defaults to API_SECRET_KEY if empty.",
    },
    {
        "key": "JWT_EXPIRE_MINUTES", "label": "JWT Expiry (minutes)", "section": "api",
        "type": "number", "default": "60",
        "description": "JWT token lifetime in minutes",
    },

    # ── Monitoring ─────────────────────────────────────────────────
    {
        "key": "GRAFANA_ADMIN_USER", "label": "Grafana Username", "section": "monitoring",
        "type": "text", "default": "admin",
        "description": "Grafana admin username",
    },
    {
        "key": "GRAFANA_ADMIN_PASSWORD", "label": "Grafana Password", "section": "monitoring",
        "type": "password", "required": True,
        "description": "Grafana admin password",
    },
    {
        "key": "PROMETHEUS_RETENTION", "label": "Data Retention", "section": "monitoring",
        "type": "text", "default": "15d",
        "description": "Prometheus TSDB retention period (e.g. 15d, 30d, 90d)",
    },
    {
        "key": "MONITORING_ADMIN_USER", "label": "BasicAuth Username", "section": "monitoring",
        "type": "text", "default": "admin",
        "description": "Username for Prometheus/Alertmanager BasicAuth (via Traefik)",
    },
    {
        "key": "MONITORING_ADMIN_PASSWORD", "label": "BasicAuth Password", "section": "monitoring",
        "type": "password", "required": True, "auto_generate": True, "generate_type": "urlsafe32",
        "description": "Password for Prometheus/Alertmanager BasicAuth — auto-hashed to traefik/dynamic/monitoring_users",
    },
    {
        "key": "LOKI_RETENTION", "label": "Loki Log Retention", "section": "monitoring",
        "type": "text", "default": "720h",
        "placeholder": "720h",
        "description": "Durée de rétention des logs dans Loki (ex: 720h = 30j, 2160h = 90j, 8760h = 1an). Le compactor supprime les chunks MinIO au-delà de cette période.",
    },

    # ── MinIO ──────────────────────────────────────────────────────
    {
        "key": "MINIO_ROOT_USER", "label": "Root Username", "section": "minio",
        "type": "text", "default": "minioadmin", "required": True,
        "description": "Compte root MinIO — accès console et administration. Modifiable après démarrage (MinIO relit l'env au redémarrage).",
    },
    {
        "key": "MINIO_ROOT_PASSWORD", "label": "Root Password", "section": "minio",
        "type": "password", "required": True, "auto_generate": True, "generate_type": "hex32",
        "description": "Mot de passe root MinIO — minimum 8 caractères. Modifiable : redémarrer minio après changement.",
    },
    {
        "key": "LOKI_S3_ACCESS_KEY", "label": "Loki S3 Access Key", "section": "minio",
        "type": "text", "default": "loki",
        "description": "Identifiant du compte MinIO dédié à Loki (créé par minio_init au premier démarrage). Changer après setup nécessite une intervention manuelle via mc.",
    },
    {
        "key": "LOKI_S3_SECRET_KEY", "label": "Loki S3 Secret Key", "section": "minio",
        "type": "password", "required": True, "auto_generate": True, "generate_type": "hex32",
        "description": "Mot de passe du compte Loki dans MinIO. Changer après setup : mettre à jour via mc admin user, puis redémarrer loki.",
    },

    # ── Event Engine ───────────────────────────────────────────────
    {
        "key": "DEDUP_TTL", "label": "Deduplication Window (s)", "section": "event_engine",
        "type": "number", "default": "60",
        "description": "Ignore duplicate events within this window (0 = disabled)",
    },
    {
        "key": "GITHUB_WEBHOOK_SECRET", "label": "GitHub Webhook Secret", "section": "event_engine",
        "type": "password", "auto_generate": True, "generate_type": "hex32",
        "description": "HMAC-SHA256 secret for validating GitHub webhook payloads",
    },
    {
        "key": "EVENT_ENGINE_ADMIN_TOKEN", "label": "Admin Token", "section": "event_engine",
        "type": "password", "required": True, "auto_generate": True, "generate_type": "urlsafe32",
        "description": "Bearer token required for /admin/* endpoints",
    },
    {
        "key": "NOTIFICATION_WEBHOOK_URL", "label": "Notification Webhook URL", "section": "event_engine",
        "type": "url",
        "description": "Generic webhook called on job completion (optional)",
    },
    {
        "key": "NOTIFICATION_SLACK_WEBHOOK", "label": "Slack Webhook URL", "section": "event_engine",
        "type": "url",
        "description": "Slack incoming webhook URL (optional)",
    },
    {
        "key": "JOB_WATCHER_INTERVAL", "label": "Job Watcher Interval (s)", "section": "event_engine",
        "type": "number", "default": "15",
        "description": "How often to poll AWX for job completion status",
    },

    # ── Gitea ──────────────────────────────────────────────────────
    {
        "key": "GITEA_DOMAIN", "label": "Domain", "section": "gitea",
        "type": "text", "derived": True,
        "description": "Gitea domain — auto-derived from Infrastructure > Domain",
    },
    {
        "key": "GITEA_ROOT_URL", "label": "Root URL", "section": "gitea",
        "type": "url", "derived": True,
        "description": "Gitea public URL — auto-derived from Infrastructure > Domain",
    },
    {
        "key": "GITEA_ADMIN_USER", "label": "Admin Username", "section": "gitea",
        "type": "text", "default": "admin", "required": True,
        "description": "Gitea admin account username (created on first start)",
    },
    {
        "key": "GITEA_ADMIN_PASSWORD", "label": "Admin Password", "section": "gitea",
        "type": "password", "required": True, "auto_generate": True, "generate_type": "urlsafe32",
        "description": "Gitea admin account password (created on first start)",
    },
    {
        "key": "GITEA_ADMIN_EMAIL", "label": "Admin Email", "section": "gitea",
        "type": "text", "default": "admin@localhost",
        "description": "Gitea admin account email address",
    },
    {
        "key": "GITEA_DB_NAME", "label": "DB Name", "section": "gitea",
        "type": "text", "default": "gitea",
        "description": "Gitea PostgreSQL database name",
    },
    {
        "key": "GITEA_DB_USER", "label": "DB User", "section": "gitea",
        "type": "text", "default": "gitea",
        "description": "Gitea PostgreSQL username",
    },
    {
        "key": "GITEA_DB_PASSWORD", "label": "DB Password", "section": "gitea",
        "type": "password", "required": True, "auto_generate": True, "generate_type": "hex32",
        "description": "Gitea database password",
    },
    {
        "key": "GITEA_SECRET_KEY", "label": "Secret Key", "section": "gitea",
        "type": "password", "required": True, "auto_generate": True, "generate_type": "hex64",
        "description": "Gitea application secret key — generate once, never change",
    },
    {
        "key": "GITEA_INTERNAL_TOKEN", "label": "Internal Token", "section": "gitea",
        "type": "password", "required": True, "auto_generate": True, "generate_type": "hex64",
        "description": "Gitea internal API token",
    },
    {
        "key": "GITEA_METRICS_TOKEN", "label": "Metrics Token", "section": "gitea",
        "type": "password", "auto_generate": True, "generate_type": "hex32",
        "description": "Bearer token for Prometheus to scrape Gitea metrics",
    },
    {
        "key": "GITEA_WEBHOOK_SECRET", "label": "Webhook Secret", "section": "gitea",
        "type": "password", "auto_generate": True, "generate_type": "hex32",
        "description": "HMAC secret for Gitea webhook signature validation",
    },
    {
        "key": "GITEA_REGISTRY_TOKEN", "label": "Registry Token", "section": "gitea",
        "type": "password", "auto_generate": True, "generate_type": "hex32",
        "description": "Token for the Gitea container registry",
    },
    {
        "key": "GITEA_LOG_LEVEL", "label": "Log Level", "section": "gitea",
        "type": "select", "default": "Warn",
        "options": ["Trace", "Debug", "Info", "Warn", "Error", "Critical"],
        "description": "Gitea log verbosity",
    },

    # ── PKI ────────────────────────────────────────────────────────
    {
        "key": "PKI_ADMIN_USER", "label": "Admin Username", "section": "pki",
        "type": "text", "default": "admin",
        "description": "PKI service admin username",
    },
    {
        "key": "PKI_ADMIN_PASSWORD", "label": "Admin Password", "section": "pki",
        "type": "password", "required": True,
        "description": "PKI service admin password",
    },
    {
        "key": "PKI_JWT_SECRET", "label": "JWT Secret", "section": "pki",
        "type": "password", "required": True, "auto_generate": True, "generate_type": "hex64",
        "description": "JWT signing secret for PKI — generate once, never change after first start",
    },
    {
        "key": "PKI_BASE_URL", "label": "Base URL", "section": "pki",
        "type": "url", "derived": True,
        "description": "Public PKI URL — auto-derived from Infrastructure > Domain",
    },
    {
        "key": "PKI_KEY_PASSPHRASE", "label": "Key Passphrase", "section": "pki",
        "type": "password",
        "description": "Optional passphrase to encrypt private keys at rest (leave empty to disable)",
    },

    # ── Disaster Recovery ──────────────────────────────────────────────────────
    {
        "key": "BACKUP_RESTIC_PASSWORD", "label": "Restic Repository Password", "section": "backup",
        "type": "password", "required": True, "auto_generate": True, "generate_type": "urlsafe32",
        "description": "Encrypts the entire backup repository (AES-256). Generate once — losing this password means losing access to all backups. Store it in a password manager separate from the server.",
    },
    {
        "key": "BACKUP_BACKEND", "label": "Storage Backend", "section": "backup",
        "type": "select", "default": "local",
        "options": ["local", "sftp", "s3", "b2"],
        "description": "Where to push backups: local (same machine — unsafe for DR), sftp (remote SSH server), s3 (AWS S3, MinIO, Wasabi, Scaleway, OVH), b2 (Backblaze B2).",
    },
    {
        "key": "BACKUP_LOCAL_PATH", "label": "Local / SFTP Repository Path", "section": "backup",
        "type": "text", "default": "/var/backups/autoflow/restic",
        "placeholder": "/var/backups/autoflow  or  sftp://user@host:22/backups/autoflow",
        "description": "Local backend: absolute path on this host. SFTP backend: sftp://user@host:port/path (e.g. sftp://backup@192.168.1.10:22/backups/autoflow).",
    },
    {
        "key": "BACKUP_S3_ENDPOINT", "label": "S3 Endpoint URL", "section": "backup",
        "type": "text",
        "placeholder": "https://s3.wasabisys.com  (empty = AWS S3)",
        "description": "S3-compatible API endpoint. Leave empty for AWS S3. Examples: http://minio:9000 (MinIO), https://s3.wasabisys.com (Wasabi), https://s3.fr-par.scw.cloud (Scaleway Paris).",
    },
    {
        "key": "BACKUP_S3_BUCKET", "label": "S3 / B2 Bucket Name", "section": "backup",
        "type": "text",
        "placeholder": "autoflow-backup",
        "description": "Bucket name for S3-compatible backends (AWS S3, MinIO, Wasabi, Scaleway) and Backblaze B2.",
    },
    {
        "key": "BACKUP_S3_ACCESS_KEY", "label": "S3 Access Key / B2 Account ID", "section": "backup",
        "type": "text",
        "description": "Access key ID for S3-compatible storage, or Backblaze B2 Account ID.",
    },
    {
        "key": "BACKUP_S3_SECRET_KEY", "label": "S3 Secret Key / B2 Application Key", "section": "backup",
        "type": "password", "sensitive": True,
        "description": "Secret access key for S3-compatible storage, or Backblaze B2 Application Key.",
    },
    {
        "key": "BACKUP_RETENTION_DAILY", "label": "Keep Daily Snapshots", "section": "backup",
        "type": "number", "default": "7",
        "description": "GFS retention — keep the last N daily snapshots (7 = one week of daily backups).",
    },
    {
        "key": "BACKUP_RETENTION_WEEKLY", "label": "Keep Weekly Snapshots", "section": "backup",
        "type": "number", "default": "4",
        "description": "GFS retention — keep the last N weekly snapshots (4 = one month of weekly backups).",
    },
    {
        "key": "BACKUP_RETENTION_MONTHLY", "label": "Keep Monthly Snapshots", "section": "backup",
        "type": "number", "default": "12",
        "description": "GFS retention — keep the last N monthly snapshots (12 = one year of monthly backups).",
    },
    {
        "key": "BACKUP_RETENTION_YEARLY", "label": "Keep Yearly Snapshots", "section": "backup",
        "type": "number", "default": "3",
        "description": "GFS retention — keep the last N yearly snapshots.",
    },
    {
        "key": "BACKUP_CRON", "label": "Backup Schedule (cron)", "section": "backup",
        "type": "text", "default": "0 2 * * *",
        "placeholder": "0 2 * * *",
        "description": "Cron expression for automated backups. Default: 2:00 AM daily. Use crontab.guru to build expressions. Click 'Install cron' in the DR section after saving.",
    },
    {
        "key": "BACKUP_RTO_HOURS", "label": "RTO Target (hours)", "section": "backup",
        "type": "number", "default": "4",
        "description": "Recovery Time Objective — maximum acceptable downtime before service is restored. Document your team's SLA here.",
    },
    {
        "key": "BACKUP_RPO_HOURS", "label": "RPO Target (hours)", "section": "backup",
        "type": "number", "default": "24",
        "description": "Recovery Point Objective — maximum acceptable data loss window. Should match your backup frequency (daily cron = 24h RPO).",
    },

    # ── Compliance & Audit ─────────────────────────────────────────
    {
        "key": "COMPLIANCE_ADMIN_TOKEN", "label": "Compliance Admin Token", "section": "compliance",
        "type": "password", "sensitive": True,
        "auto_generate": True, "generate_type": "hex32",
        "placeholder": "auto-generated",
        "description": "Bearer token required to call the security-scanner compliance endpoints. Auto-generated — copy the value into COMPLIANCE_ADMIN_TOKEN in the scanner's environment. Leave empty to disable auth (not recommended).",
    },

    # ── Advanced ───────────────────────────────────────────────────
    {
        "key": "LOG_LEVEL", "label": "Log Level", "section": "advanced",
        "type": "select", "default": "info",
        "options": ["debug", "info", "warning", "error"],
        "description": "Log verbosity for Autoflow API and Event Engine",
    },
    {
        "key": "AWX_METRICS_INTERVAL", "label": "AWX Metrics Interval (s)", "section": "advanced",
        "type": "number", "default": "60",
        "description": "AWX job metrics polling interval (0 = disabled)",
    },
    {
        "key": "DOCKER_GID", "label": "Docker Socket GID", "section": "advanced",
        "type": "number", "default": "999",
        "description": "GID of /var/run/docker.sock on the host. Verify: stat -c '%g' /var/run/docker.sock",
    },
    {
        "key": "SCAN_INTERVAL", "label": "Security Scan Interval (s)", "section": "advanced",
        "type": "number", "default": "21600",
        "description": "Trivy CVE scan frequency (default: 6 hours)",
    },
    {
        "key": "TRIVY_VERSION", "label": "Trivy Version", "section": "advanced",
        "type": "text", "default": "0.63.0", "readonly": True,
        "description": "Trivy scanner version (pinned)",
    },
]
