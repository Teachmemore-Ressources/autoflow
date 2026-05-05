---
title: Composants
---

# Composants

Fiche technique de chaque service de la stack Autoflow.

---

## Services AWX

### awx_web
| | |
|---|---|
| **Image** | `autoflow/awx-patched:24.6.1` |
| **Container** | `autoflow_awx_web` |
| **Réseau interne** | `:80` |
| **URL externe** | `https://awx.<DOMAIN>` |
| **Dépendances** | postgres, redis, awx_migrate |
| **Volumes** | `awx_projects`, `awx_rsyslog`, `receptor_socket` |

Serveur web AWX. Sert l'interface utilisateur Django et l'API REST (`/api/v2/`). Communique avec `awx_task` via Redis (channels) et PostgreSQL. Le trafic WebSocket pour le streaming des logs de jobs passe par `awx_web`.

!!! info "Image patchée"
    L'image `autoflow/awx-patched` est construite depuis l'image officielle AWX avec des patches mineurs (branding, configuration Traefik). Voir `awx/Dockerfile.patched` et `make awx-build`.

---

### awx_task
| | |
|---|---|
| **Image** | `autoflow/awx-patched:24.6.1` |
| **Container** | `autoflow_awx_task` |
| **Dépendances** | awx_web, postgres, redis, receptor |
| **Volumes** | `awx_projects`, Docker socket (`/var/run/docker.sock`) |

Workers Celery qui exécutent les tâches AWX en arrière-plan : synchronisation de projets Git, exécution de jobs, envoi de notifications. `awx_task` monte le socket Docker pour pouvoir créer les conteneurs EE via Receptor.

---

### receptor
| | |
|---|---|
| **Image** | `autoflow/receptor:local` |
| **Container** | `autoflow_receptor` |
| **Volumes** | `receptor_socket` (socket Unix partagé avec awx_task) |

Receptor est le **mesh d'exécution** d'AWX. Il reçoit les demandes d'exécution de job depuis `awx_task` et lance les conteneurs Execution Environment via Docker. Partage son socket Unix avec `awx_task` via un volume.

---

### awx_migrate *(init job)*
| | |
|---|---|
| **Image** | `autoflow/awx-patched:24.6.1` |
| **Container** | `autoflow_awx_migrate` |

S'exécute **une seule fois** au démarrage, applique les migrations Django de la base de données AWX, puis s'arrête. AWX ne démarre pas si ce conteneur échoue.

---

## Services GitOps

### gitea
| | |
|---|---|
| **Image** | `gitea/gitea:1.23-rootless` |
| **Container** | `autoflow_gitea` |
| **Réseau interne** | `:3001` (HTTP) |
| **URL externe** | `https://git.<DOMAIN>` |
| **SSH** | `:2222` |
| **Dépendances** | gitea_postgres |
| **Volumes** | `gitea_data` |

Gitea fournit :

- **Git self-hosted** : dépôts de playbooks Ansible, IaC, workflows
- **Container Registry** : images Docker (Execution Environments, images custom)
- **Gitea Actions** : CI/CD compatible GitHub Actions
- **Webhooks** : événements push/tag/release envoyés à l'Event Engine

!!! tip "Credentials par défaut"
    Le compte admin est créé au premier démarrage avec les valeurs `GITEA_ADMIN_USER` et `GITEA_ADMIN_PASSWORD` du fichier `.env`.

---

### gitea_postgres
| | |
|---|---|
| **Image** | `postgres:15.17-alpine` |
| **Container** | `autoflow_gitea_postgres` |
| **Volume** | `gitea_postgres_data` |

Instance PostgreSQL **dédiée à Gitea**, isolée de la base AWX. Ne partage aucun volume avec `postgres`.

---

### act_runner
| | |
|---|---|
| **Image** | `gitea/act_runner:nightly` |
| **Container** | `autoflow_act_runner` |
| **Dépendances** | gitea |
| **Volumes** | Docker socket, `runner_data` |

Runner Gitea Actions. Exécute les workflows CI/CD dans des conteneurs Docker. Enregistré dans Gitea avec le token `GITEA_RUNNER_TOKEN`. Labels : `autoflow, linux, docker, ansible`.

---

### ee_builder
| | |
|---|---|
| **Container** | `autoflow_ee_builder` |
| **Réseau interne** | `:8003` |
| **Dépendances** | gitea |

Service de build des Execution Environments. Utilise `ansible-builder` pour construire les images EE à partir des définitions dans `execution-environments/`. Pousse les images vers le registry Gitea.

---

## Services Autoflow

### autoflow_api
| | |
|---|---|
| **Container** | `autoflow_api` |
| **Réseau interne** | `:8000` |
| **URL externe** | `https://api.<DOMAIN>` |

API FastAPI qui expose les fonctionnalités AWX avec authentification JWT. Sert de proxy sécurisé pour les intégrations tierces. Inclut les endpoints de métriques AWX pour Prometheus.

---

### event_engine
| | |
|---|---|
| **Container** | `autoflow_event_engine` |
| **Réseau interne** | `:8001` |
| **URL externe** | `https://api.<DOMAIN>/events/` (via Traefik) |
| **Volumes** | `event-engine/rules.yml`, `event-engine/schedules.yml` |

Récepteur de webhooks et moteur de routage. Reçoit des événements (GitHub, Gitea, Alertmanager, génériques), les matche contre `rules.yml`, et lance le job AWX correspondant. Supporte la déduplication et les événements planifiés (cron).

---

### deploy_wizard
| | |
|---|---|
| **Processus** | `uvicorn main:app --port 9000` (hors Docker) |
| **Accès** | `http://localhost:9000` (tunnel SSH recommandé) |

Interface web de configuration et déploiement. Gère le fichier `.env`, génère les secrets, effectue les pré-flight checks et pilote le déploiement. Sécurisé par `WIZARD_TOKEN` (Basic Auth).

---

### pki
| | |
|---|---|
| **Container** | `autoflow_pki` |
| **Réseau interne** | `:8004` |
| **URL externe** | `https://pki.<DOMAIN>` |
| **Volumes** | `pki_data` (CA, certificats, CRL) |

Service de PKI interne. Génère et signe les certificats TLS utilisés par Traefik pour tous les sous-domaines. Expose une interface web pour visualiser et révoquer les certificats. Protégé par JWT (`PKI_JWT_SECRET`).

---

### security_scanner
| | |
|---|---|
| **Container** | `autoflow_security_scanner` |
| **Réseau interne** | `:8002` |
| **Volumes** | Docker socket (lecture seule) |

Scanner de sécurité basé sur Trivy. Scanne toutes les images Docker de la stack à intervalles réguliers (`SCAN_INTERVAL`). Génère des rapports de conformité aux frameworks NIST SP 800-53, CIS Controls v8, ISO 27001:2022, PCI-DSS v4.0, SOC 2 TSC.

---

## Services Data

### postgres
| | |
|---|---|
| **Image** | `postgres:15.17-alpine` |
| **Container** | `autoflow_postgres` |
| **Volume** | `postgres_data` |

Base de données principale d'AWX. Stocke les jobs, inventaires, credentials, templates, projets, utilisateurs et toute la configuration AWX.

---

### redis
| | |
|---|---|
| **Image** | `redis:7.4.8-alpine` |
| **Container** | `autoflow_redis` |
| **Volume** | `redis_data` |

Utilisé par AWX comme :

- **Cache** de session et de données (base 1)
- **Broker Celery** pour la queue de jobs (base 0)
- **Channel Layer** Django Channels pour les WebSockets

---

### minio
| | |
|---|---|
| **Image** | `minio/minio:RELEASE.2024-10-13T13-34-11Z` |
| **Container** | `autoflow_minio` |
| **Volume** | `minio_data` |

Stockage objet compatible S3 utilisé comme backend de Loki pour stocker les chunks de logs. Compte root (`MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD`) + compte dédié Loki (`LOKI_S3_ACCESS_KEY`/`LOKI_S3_SECRET_KEY`).

---

## Services Observabilité

### prometheus
| | |
|---|---|
| **Image** | `prom/prometheus:v3.11.1` |
| **Container** | `autoflow_prometheus` |
| **URL externe** | `https://prometheus.<DOMAIN>` (BasicAuth) |
| **Volume** | `prometheus_data` |

Collecte et stocke les métriques de tous les services. Configure via `monitoring/prometheus/prometheus.yml`. Rétention : `PROMETHEUS_RETENTION` (défaut: 15 jours).

---

### grafana
| | |
|---|---|
| **Image** | `grafana/grafana:12.4.2` |
| **Container** | `autoflow_grafana` |
| **URL externe** | `https://grafana.<DOMAIN>` |
| **Volume** | `grafana_data` |

Visualisation des métriques, logs et traces. Datasources préconfigurées : Prometheus, Loki, Tempo. Dashboards AWX, système, Gitea, PKI inclus.

---

### loki
| | |
|---|---|
| **Image** | `grafana/loki:3.4.2` |
| **Container** | `autoflow_loki` |
| **Volume** | `loki_data` + MinIO (chunks) |
| **Rétention** | `LOKI_RETENTION` (défaut: 720h = 30 jours) |

Agrégateur de logs. Reçoit les logs de Promtail, les indexe et les stocke dans MinIO. Requêtable depuis Grafana avec LogQL.

---

### promtail
| | |
|---|---|
| **Image** | `grafana/promtail:3.4.2` |
| **Container** | `autoflow_promtail` |
| **Volumes** | `/var/lib/docker/containers` (read-only) |

Agent de collecte de logs. Scrape les logs de **tous les conteneurs Docker** sur l'hôte et les envoie à Loki avec des labels automatiques.

---

### tempo
| | |
|---|---|
| **Image** | `grafana/tempo:2.6.1` |
| **Container** | `autoflow_tempo` |
| **Volume** | `tempo_data` |

Backend de tracing distribué. Reçoit les traces des services Autoflow (API, Event Engine) via l'OTel Collector et les expose à Grafana.

---

### otel_collector
| | |
|---|---|
| **Image** | `otel/opentelemetry-collector-contrib:0.111.0` |
| **Container** | `autoflow_otel_collector` |
| **Réseau interne** | `:4317` (OTLP/gRPC), `:13133` (health) |

Pipeline OpenTelemetry. Reçoit les traces des services (`OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317`) et les relaie à Tempo.

---

### alertmanager
| | |
|---|---|
| **Image** | `prom/alertmanager:v0.32.0` |
| **Container** | `autoflow_alertmanager` |
| **URL externe** | `https://alertmanager.<DOMAIN>` (BasicAuth) |

Gestion des alertes Prometheus. Route les alertes vers Slack, webhook ou email selon les règles configurées dans `monitoring/alertmanager/`.

---

### node_exporter
| | |
|---|---|
| **Image** | `prom/node-exporter:v1.9.1` |
| **Container** | `autoflow_node_exporter` |

Exporte les métriques système de l'hôte (CPU, RAM, disque, réseau) vers Prometheus.

---

### postgres_exporter
| | |
|---|---|
| **Image** | `prometheuscommunity/postgres-exporter:v0.19.1` |
| **Container** | `autoflow_postgres_exporter` |

Exporte les métriques PostgreSQL (connexions, cache hit ratio, taille des tables, locks) vers Prometheus.

---

### redis_exporter
| | |
|---|---|
| **Image** | `oliver006/redis_exporter:v1.82.0` |
| **Container** | `autoflow_redis_exporter` |

Exporte les métriques Redis (mémoire, commandes/sec, clients connectés, evictions) vers Prometheus.

---

## Récapitulatif des ports exposés sur l'hôte

| Port | Service | Notes |
|---|---|---|
| **80** | Traefik HTTP | Redirige vers 443 |
| **443** | Traefik HTTPS | Tout le trafic web |
| **2222** | Gitea SSH | Git push/pull via SSH |
| **9000** | Deploy Wizard | Accès local uniquement (tunnel SSH) |

!!! success "Bonne pratique"
    En production, firewall tout sauf 80, 443 et 2222 (si SSH Git utilisé). Le wizard sur 9000 ne doit jamais être exposé directement sur internet — utiliser un tunnel SSH.
