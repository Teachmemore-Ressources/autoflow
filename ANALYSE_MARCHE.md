# 🔍 ANALYSE COMPLÈTE — ÉVALUATION MARCHÉ AUTOFLOW
*Alternative à Ansible Automation Platform (AAP) / AWX*
*Date : 2026-05-23 — Analysé sur commit courant*

---

> **Note préliminaire importante** : Autoflow n'est PAS un remplacement d'AWX — c'est une **plateforme d'intégration** qui emballe AWX avec tout l'écosystème nécessaire à sa mise en production (GitOps, monitoring, PKI, sauvegardes, webhooks). Cette distinction est fondamentale pour comprendre le positionnement et les limites de la solution.

---

## COUCHE 1 — ARCHITECTURE & PATTERNS TECHNIQUES

### 1.1 Vue d'ensemble architecturale

**Pattern architectural principal : Intégration de plateforme (Platform Integration / Opinionated Distribution)**

Autoflow n'est ni un monolithe ni une architecture microservices au sens strict. C'est un **assemblage opinionné** de services open-source existants, reliés par :
- Des **services de colle** développés sur mesure (FastAPI Python)
- Un **reverse proxy TLS** (Traefik) comme point d'entrée unique
- Un **fichier `.env` centralisé** comme source de vérité de configuration
- Un **Deploy Wizard** guidant l'installation complète

**Architecture en couches (Layered + Event-driven pour la partie Event Engine) :**

```
┌─────────────────────────────────────────────────────────────────┐
│  COUCHE RÉSEAU / INGRESS                                         │
│  Traefik v2.11  (TLS termination, routing par sous-domaine)      │
└──────────────────┬──────────────────────────────────────────────┘
                   │
        ┌──────────┴──────────┐
        │                     │
┌───────▼──────────┐  ┌───────▼──────────┐
│  COUCHE AUTOMATION│  │  COUCHE GITOPS    │
│  AWX 24.6.1       │  │  Gitea 1.23       │
│  ├─ awx_web       │  │  ├─ Git repos     │
│  ├─ awx_task      │  │  ├─ OCI Registry  │
│  ├─ receptor      │  │  └─ Act Runner    │
│  └─ awx_migrate   │  └──────────────────┘
└───────┬──────────┘
        │
┌───────▼────────────────────────────────────────────────────────┐
│  COUCHE SERVICES AUTOFLOW (custom FastAPI microservices)        │
│  ├─ Autoflow API (port 8000)  — proxy enrichi AWX + auth JWT    │
│  ├─ Event Engine (port 8001)  — webhooks → AWX (Redis + retry)  │
│  ├─ Security Scanner (8002)   — Trivy CVE + conformité          │
│  ├─ EE Builder (port 8003)    — build Execution Environments     │
│  ├─ PKI Service (port 8004)   — CA interne + LDAP/AD            │
│  └─ Deploy Wizard (port 9000) — UI config + déploiement guidé   │
└───────┬────────────────────────────────────────────────────────┘
        │
┌───────▼────────────────────────────────────────────────────────┐
│  COUCHE DONNÉES                                                  │
│  ├─ PostgreSQL 15.17 (AWX)    ├─ PostgreSQL 15.17 (Gitea)       │
│  ├─ Redis 7.4.8               └─ MinIO (S3 backend Loki)        │
└───────┬────────────────────────────────────────────────────────┘
        │
┌───────▼────────────────────────────────────────────────────────┐
│  COUCHE OBSERVABILITÉ (LGTM Stack)                              │
│  Prometheus 3.11 → Grafana 12.4 → Alertmanager 0.32            │
│  Loki 3.4 + Promtail ←→ MinIO (stockage logs)                  │
│  Tempo 2.6 + OTel Collector (tracing distribué)                 │
│  Node/PG/Redis Exporters                                        │
└────────────────────────────────────────────────────────────────┘
```

**Dépendances inter-modules :**
- Event Engine → AWX API (HTTP) + Redis (persistence)
- Autoflow API → AWX API (HTTP, proxy thin)
- Security Scanner → Docker socket (Trivy) + AWX API
- EE Builder → Docker socket + Gitea Registry
- PKI → Traefik certs volume (déploiement direct certificats)
- Deploy Wizard → Docker socket + tous les services (orchestrateur)

**Couplage identifié :**
- ⚠️ Le Deploy Wizard a un couplage fort avec le système de fichiers hôte (`.env`, Docker socket, `traefik/certs/`, `/etc/docker/daemon.json`, `/etc/hosts`) — c'est intentionnel mais crée une dépendance au système d'exploitation.
- ✅ Les services Autoflow sont faiblement couplés entre eux (communication via HTTP uniquement, pas de dépendances directes de code).
- ⚠️ `awx/settings.py` référence `DOMAIN` via `os.environ` pour construire `DEFAULT_CONTAINER_RUN_OPTIONS` — couplage au runtime Docker.

### 1.2 Stack technique

| Composant | Version détectée | Rôle | Évaluation vs concurrents |
|-----------|-----------------|------|--------------------------|
| **AWX** | 24.6.1 (patched) | Moteur automation | ✅ Parité exacte avec AWX/AAP |
| **Python** | 3.10 (runtime services) | Services custom | ✅ Standard industrie |
| **FastAPI** | 0.115.5 | Framework API | ✅ Moderne, performant, OpenAPI natif |
| **Pydantic v2** | 2.10.3 | Validation | ✅ Recommandé |
| **Redis** | 7.4.8-alpine | Cache/Queue | ✅ Pinné, sécurisé |
| **PostgreSQL** | 15.17-alpine | DB | ✅ Stable LTS |
| **Traefik** | v2.11 | Reverse proxy | ✅ Standard cloud-native |
| **Prometheus** | v3.11.1 | Métriques | ✅ CNCF standard |
| **Grafana** | 12.4.2 | Dashboards | ✅ Leader marché |
| **Loki** | 3.4.2 | Logs | ✅ Bonne intégration |
| **Tempo** | 2.6.1 | Traces | ✅ OTel compatible |
| **Gitea** | 1.23-rootless | Git+Registry | ⚠️ Alternative à GitLab/GitHub, moins connue |
| **MinIO** | 2024-10-13 | S3 backend | ✅ AGPL-3.0 (voir licences) |
| **Trivy** | 0.63.0 | CVE scanning | ✅ Leader open-source |
| **Restic** | - | Backups | ✅ BSD-2, moderne |
| **SOPS + Age** | - | Secrets | ✅ Standard moderne |
| **slowapi** | 0.1.9 | Rate limiting | ✅ Pour FastAPI |
| **PyJWT** | 2.10.1 | JWT | ✅ |
| **cryptography** | - | PKI | ✅ Bibliothèque officielle |
| **ldap3** | - | LDAP/AD | ✅ |
| **bcrypt** | - | Hachage passwords | ✅ |
| **opentelemetry** | 1.27.0 | Tracing | ✅ Standard CNCF |

**⚠️ Alertes licences enterprise :**
- Grafana (AGPL-3.0) : usage interne sans redistribution = OK. SaaS ou revente = problème.
- Loki (AGPL-3.0) : même contrainte.
- Tempo (AGPL-3.0) : idem.
- MinIO (AGPL-3.0) : idem.
- **Autoflow lui-même n'a pas de licence déclarée** (`LICENSE` absent du projet) — **BLOQUANT** pour la commercialisation.

**Frein à l'adoption enterprise :**
- Gitea vs GitLab/GitHub Enterprise : certains grands comptes ont déjà une infrastructure Git existante. La dépendance à Gitea comme source de code pour AWX est configurable (AWX supporte tout SCM) mais le wizard ne le documente pas.
- Stack de 25+ conteneurs : complexité perçue élevée pour les équipes non-Docker.

### 1.3 Qualité du code & maintenabilité

**Observations positives :**
- Docstrings dans tous les services FastAPI (module-level, fonctions clés)
- Structure cohérente : chaque service a `main.py`, `settings.py`, `tracing.py`, `requirements.txt`
- Pydantic v2 pour la validation des entrées (validators `field_validator`)
- Séparation claire : routers/ pour les endpoints, modules dédiés pour la logique métier
- Gestion propre du cycle de vie (`asynccontextmanager lifespan`)
- Logs structurés JSON pour l'audit (main.py services/api/)

**Observations négatives :**
- **Absence de linting/formatter** : aucun `.flake8`, `.pylintrc`, `mypy.ini`, `ruff.toml`, ni pre-commit hooks (`.pre-commit-config.yaml` absent)
- **`deploy-wizard/main.py` fait 3600+ lignes** : fichier monolithique, devrait être découpé en modules
- **Duplication de code** : la fonction `_human_dur()` est dupliquée dans `routers/compliance.py` et `routers/jobs_history.py` — devrait être dans un module `utils.py`
- **Magic strings** : nombreuses chaînes littérales sans constantes (`"successful"`, `"failed"`, `"admin"`, etc.)
- **Types manquants** : la Wizard API utilise `body: dict = {}` sans schémas Pydantic pour les endpoints critiques
- **Commentaires en mélange FR/EN** : dans `awx/settings.py`, `main.py` de la wizard — incohérence
- **`/tmp/compliance_latest.html`** comme cache : non persisté entre redémarrages container, devrait être en volume

**Note de maintenabilité : 5.5/10**
*Justification : code fonctionnel, bien structuré pour les petits services (Event Engine, PKI) mais la wizard géante (3600+ lignes) et l'absence d'outillage qualité (linting, typing strict, CI tests) constituent un risque majeur de dérive.*

---

## COUCHE 2 — DONNÉES, MODÈLES & FLUX

### 2.1 Modèles de données

Autoflow ne définit pas de modèles de données propres — il s'appuie sur AWX (Django ORM + PostgreSQL) et des fichiers JSON locaux pour ses propres services.

**AWX (Django ORM → PostgreSQL `autoflow_postgres`)** :
- `Job`, `JobTemplate`, `Inventory`, `Host`, `Group`, `Credential`, `Project`, `Schedule`, `WorkflowJob`, `WorkflowJobTemplate`, `Organization`, `Team`, `User`, `Role` — modèles AWX complets (hérités)

**PKI Service (JSON fichier `/data/pki/db.json`)** :
```json
{
  "certificates": { "<serial_hex>": { "common_name", "ca_name", "not_before", "not_after", "cert_type", "wildcard", "sans", "fingerprint_sha256", "issued_by", "key_size" }},
  "cas": { "<ca_name>": { "common_name", "not_after", "serial", "fingerprint", "created_at" }},
  "revoked_serials": [...],
  "revocations": { "<serial>": { "reason", "revoked_at", "revoked_by" }},
  "deployed": { "traefik_wildcard": { "serial", "deployed_at", "cert_path", "domain" }}
}
```
- **⚠️ Fichier JSON non-threadé** : verrou fichier POSIX (`fcntl.flock`) — fonctionne en single-node, pas en multi-instance

**PKI Users (`/data/pki/users.json`)** :
```json
{ "<username>": { "password_hash", "role", "created_at" }}
```

**Event Engine (Redis, pas de schéma formel)** :
- Clé de file `autoflow:events:pending` (liste Redis)
- Clé de retry `autoflow:events:retry:{id}`
- DLQ `autoflow:events:dlq` (sorted set)
- Dédup `autoflow:dedup:{hash}` (clé avec TTL)

**Wizard Audit Log (`/wizard-audit.log`)** :
```json
{ "ts", "ip", "action", "keys" }
```

**Problèmes de conception identifiés :**
- ⚠️ La PKI stocke tout dans un seul fichier JSON sans index — performance dégradée au-delà de quelques milliers de certificats
- ⚠️ `USERS_FILE` de la PKI stocke les hachages bcrypt mais pas de sel explicite séparé (bcrypt l'intègre nativement — OK)
- ✅ Les relations AWX (1-N, N-N entre Organisations/Équipes/Utilisateurs/Templates) sont gérées par AWX/Django, pas redéfinies

### 2.2 Flux de données applicatifs

**Cycle de vie complet d'un job (webhook → résultat) :**

```
1. Source externe (GitHub push, Alertmanager, CI)
   │
   ▼
2. Event Engine: POST /webhook/github
   ├─ Vérification signature HMAC-SHA256
   ├─ Rate limiting (slowapi)
   ├─ Déduplication (Redis TTL hash)
   ├─ Persistance Redis (EventStore)
   └─ 202 Accepted

3. RetryWorker (background asyncio)
   ├─ Dépile la file Redis
   ├─ RuleEngine.resolve() → job_template_id + extra_vars
   ├─ AWXClient.launch_job() → POST /api/v2/job_templates/{id}/launch/
   ├─ En cas d'erreur : backoff exponentiel (30s→2min→5min→10min→20min)
   └─ Après 5 échecs : Dead Letter Queue

4. AWX (job en base + queue Celery)
   │
   ▼
5. awx_task (Celery worker)
   ├─ Pickup du job
   ├─ Dispatch via Receptor
   └─ Création container EE (Docker socket)

6. EE Container (docker run)
   ├─ git clone depuis Gitea
   ├─ ansible-runner → playbook
   ├─ SSH → hôtes cibles
   └─ Streaming output via WebSocket → awx_web

7. Autoflow API (job watcher background)
   ├─ Polling AWX /api/v2/jobs/{id}/ toutes les N secondes
   ├─ Détection statut terminal
   └─ Notification (webhook + Slack Block Kit)
```

**Gestion des états (machines à états) :**
AWX implémente la machine à états : `new → pending → waiting → running → successful | failed | error | canceled`

L'Event Engine ajoute ses propres états : `pending → processing → done | retry | dlq`

**⚠️ Transitions manquantes :**
- Pas de mécanisme de `timeout` de job au niveau Autoflow (AWX a le sien)
- Pas d'annulation de job via l'Event Engine (possible via API AWX directement)

### 2.3 Gestion de la configuration & inventaires

**Inventaires :**
Gérés entièrement par AWX — Autoflow n'ajoute pas de couche.
- ✅ AWX supporte : inventaires statiques (YAML/INI), sources dynamiques (EC2, Azure, GCE, vCenter, OpenStack, scripts custom)
- ✅ Inventaires dans les projets Git (playbooks/network/inventory/hosts.yml)
- ✅ Variables group_vars et host_vars dans les repos Gitea
- ❌ Pas d'interface Autoflow pour créer/gérer les inventaires — tout via UI AWX ou API AWX

**Stockage des variables :**
- Variables Ansible dans les projets Git (versionnées)
- Credentials AWX (chiffrés avec `AWX_SECRET_KEY`, stockés en PostgreSQL)
- Variables d'environnement dans `.env` (chiffrable avec SOPS+Age)

**Secrets management :**
- ✅ AWX chiffre tous les credentials avec la `SECRET_KEY` (AES-256)
- ✅ SOPS + Age pour chiffrer le `.env` au repos et dans git
- ✅ PKI pour la gestion des certificats
- ⚠️ Pas d'intégration HashiCorp Vault, AWS Secrets Manager, Azure Key Vault (AWX supporte ces backends mais non configuré)
- ⚠️ La `AWX_SECRET_KEY` ne peut jamais changer (documenté comme limitation — ADR-001)

---

## COUCHE 3 — SYSTÈMES FONCTIONNELS CRITIQUES

### 3.1 Authentification & Autorisation

**Tableau récapitulatif par service :**

| Service | Mécanisme | Rôles/RBAC | LDAP/AD | MFA | SSO |
|---------|-----------|------------|---------|-----|-----|
| **AWX** | Local + OAuth2 + LDAP + SAML | ✅ Complet (Organisations, Équipes, Rôles granulaires) | ✅ | ❌ | ✅ SAML2 |
| **Autoflow API** | JWT Bearer + X-API-Key | ❌ Un seul utilisateur admin | ❌ | ❌ | ❌ |
| **PKI** | JWT (local + LDAP/AD) | ✅ admin/operator/viewer | ✅ | ❌ | ❌ |
| **Event Engine** | Bearer token (admin) | ❌ Binaire : admin ou public | ❌ | ❌ | ❌ |
| **Deploy Wizard** | HTTP Basic (WIZARD_TOKEN) | ❌ Monoutilisateur | ❌ | ❌ | ❌ |
| **Gitea** | Local + OAuth2 + LDAP + SAML | ✅ | ✅ | ✅ (TOTP) | ✅ |
| **Grafana** | BasicAuth via Traefik + local | ✅ (Grafana natif) | ✅ | ❌ | ✅ |
| **Monitoring** | BasicAuth Traefik | ❌ Unique | ❌ | ❌ | ❌ |

**Analyse de l'auth Autoflow API (`services/api/routers/auth.py`) :**
- JWT HS256, expiration configurable (défaut 60 min)
- `POST /auth/token` → OAuth2 password flow avec rate limiting 5/min ✅
- `POST /auth/refresh` → rafraîchissement de token ✅
- `GET /auth/me` → identité courante ✅
- Sensibilité query params redactée dans les logs audit ✅
- **⚠️ Un seul compte utilisateur** : `API_USERNAME`/`API_PASSWORD` (toujours "admin") — pas multi-utilisateurs
- **⚠️ Pas de révocation de token** : un JWT valide reste valide jusqu'à expiration, même si le mot de passe change
- **⚠️ CORS par défaut `"*"`** dans les settings — doit être restreint en production

**Analyse PKI auth (`services/pki/app/main.py`) :**
- ✅ JWT HS256 avec `jti` (token ID unique, préventif contre replay)
- ✅ LDAP/AD avec support AD récursif (LDAP_MATCHING_RULE_IN_CHAIN)
- ✅ RBAC 3 niveaux : admin/operator/viewer avec permissions granulaires
- ✅ Fallback local si LDAP indisponible configurable
- ✅ Security headers middleware (X-Content-Type-Options, X-Frame-Options, etc.)
- ✅ Audit log complet (action, user, role, ip, resource)
- ⚠️ bcrypt `rounds=12` — bon équilibre sécurité/performance
- ⚠️ `USERS_FILE` stocké en JSON local — pas de DB relationnelle, limite la gestion multi-utilisateurs

**Lacunes enterprise critiques :**
- ❌ **Pas de MFA** sur aucun service custom Autoflow
- ❌ **Pas de SSO** au niveau plateforme (chaque service a son auth indépendante)
- ❌ **Pas de révocation de tokens JWT** (ni liste noire)
- ❌ **Autoflow API mono-utilisateur** : inadapté au multi-équipe
- ❌ **Deploy Wizard accessible** via HTTP Basic sur le réseau — WIZARD_TOKEN sans expiration ni rotation forcée

### 3.2 Moteur d'exécution des jobs

**Mécanisme d'exécution (hérité d'AWX) :**
- AWX utilise **ansible-runner** via **Receptor** (mesh de communication)
- Les jobs s'exécutent dans des **Execution Environments** (conteneurs Docker)
- Le **Docker socket** est bind-monté dans `awx_task` pour lancer les containers EE
- `CONTAINER_RUNTIME = 'docker'` (configuré dans `awx/settings.py`)

**File de jobs :**
- AWX utilise Celery + Redis pour la file de jobs
- ✅ Priorités, retry, timeout natifs AWX
- ✅ Annulation de job via API AWX
- ✅ Concurrence gérée par AWX (nombre de workers Celery)

**Exécution dans Autoflow :**
```
awx_web (Django REST) → awx_task (Celery) → receptor → Docker EE container → playbook
```

**Comparaison avec AWX natif :**
| Fonctionnalité | Autoflow/AWX | AWX standalone |
|---------------|-------------|----------------|
| Execution Environments | ✅ Identique | ✅ |
| Receptor mesh | ✅ Même implémentation | ✅ |
| Isolated execution | ✅ Container par job | ✅ |
| Multi-node execution | ❌ Single-node Docker | ✅ Multi-node K8s |
| Instance Groups | ✅ Configuré au déploiement | ✅ |

**⚠️ Limitation significative :** `DEFAULT_CONTAINER_RUN_OPTIONS` dans `awx/settings.py` utilise `host-gateway` comme gateway Docker pour atteindre Traefik/Gitea depuis les EE containers. Cette configuration fonctionne sur Docker standard mais **ne fonctionnerait pas tel quel sur Kubernetes** ou Podman rootless sans adaptation.

### 3.3 Scheduling & Automatisation

**Scheduling natif AWX (hérité) :**
- ✅ Schedules cron sur les Job Templates
- ✅ Gestion des timezones
- ✅ Activation/désactivation des schedules
- ✅ Schedules sur les Workflow Job Templates

**Event Engine Scheduler :**
- ✅ Schedules cron définis dans `services/event-engine/schedules.yml` + `event-engine/schedules.yml`
- ✅ Hot-reload sans redémarrage (`POST /admin/schedules/reload`)
- ✅ Déclenche des events qui sont ensuite routés vers des AWX templates par le rule engine

**Workflows conditionnels (AWX) :**
- ✅ Workflows AWX avec nœuds conditionnels (success/failure/always)
- ✅ Approbations manuelles dans les workflows
- Accessibles via l'UI AWX uniquement, pas d'abstraction Autoflow

**Ce qui manque pour la production enterprise :**
- ❌ Pas de système de calendrier d'exclusion (maintenance windows) au niveau Autoflow
- ❌ Pas de déclencheur basé sur le résultat d'un job précédent au niveau Event Engine
- ❌ Pas de visualisation graphique des schedules dans l'UI Autoflow (dépend de l'UI AWX)

### 3.4 Gestion des projets & sources de code

**Sources de code :**
- AWX projets pointent vers des repos Gitea internes (ou GitHub/GitLab externes)
- ✅ Webhooks Gitea → Event Engine → AWX project update (configuré dans les ADR)
- ✅ Branches, tags, SHA commits supportés nativement par AWX
- ✅ CI/CD Gitea Actions pour tester les playbooks avant merge (`.gitea/workflows/`)
- ✅ Pipeline de build EE dans Gitea Actions (`execution-environments/.gitea/workflows/ee-build.yml`)

**Synchronisation :**
- AWX gère le `git clone` des projets
- La PKI CA est montée dans les EE containers pour que git clone fonctionne sur HTTPS interne
- ✅ `GIT_SSL_CAINFO=/etc/autoflow/ca.crt` injecté via `DEFAULT_CONTAINER_RUN_OPTIONS`

---

## COUCHE 4 — SÉCURITÉ & CONFORMITÉ ENTERPRISE

### 4.1 Sécurité applicative

**Protections en place :**

| Vecteur | Status | Détail |
|---------|--------|--------|
| **CSRF** | ✅ via AWX Django | `CSRF_COOKIE_SECURE=True`, cookie Secure |
| **XSS** | ✅ Headers PKI | `X-XSS-Protection`, `X-Content-Type-Options` sur PKI. **⚠️ Absents des autres services FastAPI** |
| **Injection SQL** | ✅ ORM Django AWX | Pas de SQL raw dans le code custom |
| **SSRF** | ⚠️ Partiel | L'Event Engine fait des requêtes vers AWX configurable — pas de validation d'URL |
| **Path traversal** | ✅ | PKI valide les noms avec `SAFE_NAME_RE`, serials avec `SERIAL_RE` |
| **Mass assignment** | ✅ Pydantic | Validation stricte des entrées |
| **Rate limiting** | ✅ | `slowapi` sur tous les services, rates configurables |
| **Audit trail** | ✅ | JSON audit log sur Autoflow API + PKI. ⚠️ Absent de l'Event Engine |
| **TLS** | ✅ | Traefik termine TLS, PKI interne génère les certificats |
| **HTTP Security headers** | ⚠️ | Complets sur PKI uniquement. Manquants sur API, Event Engine, Security Scanner |
| **HMAC webhook** | ✅ | GitHub webhook valide avec `hmac.compare_digest` |

**Chiffrement des secrets :**
- ✅ Credentials AWX chiffrés en DB (AES, clé `AWX_SECRET_KEY`)
- ✅ `.env` chiffrable avec SOPS+Age
- ✅ Clés PKI chiffrées avec passphrase optionnelle
- ✅ Mots de passe bcrypt (rounds=12)
- ✅ Redis avec authentification (REDIS_PASSWORD)
- ✅ PostgreSQL avec mot de passe
- ⚠️ Clés PKI écrites sur disque dans le volume `pki_data` — accessible à quiconque a accès au volume Docker

**Audit trail complet :**
- ✅ Autoflow API : middleware JSON audit log (méthode, path, status, durée, IP, user-agent)
- ✅ PKI : audit log structuré (action, user, role, IP, resource)
- ✅ Deploy Wizard : wizard-audit.log (IP, action)
- ✅ AWX : Activity Stream natif (complet, qui a fait quoi)
- ⚠️ Event Engine : logs applicatifs mais pas d'audit structuré formel

### 4.2 Conformité & Standards

**API REST :**
- ✅ Autoflow API expose Swagger UI (`/docs`) et ReDoc (`/redoc`)
- ✅ PKI expose une API REST complète
- ✅ AWX API v2 exhaustive et documentée
- ⚠️ Pas de versionnage d'API Autoflow (path `/api/v2/` non implémenté, `/` direct)
- ⚠️ Pas de changelog machine-readable (OpenAPI) pour les breaking changes

**Rate limiting :**
- ✅ `slowapi` sur tous les services (configurable via env vars)
- ✅ `/auth/token` : 5/minute (anti-brute-force)
- ✅ PKI login : 10/minute
- ✅ Webhooks Event Engine : taux configurable

**HTTPS et headers sécurité :**
- ✅ HTTPS forcé sur tous les services via Traefik (redirect HTTP→HTTPS)
- ✅ HSTS sur PKI (uniquement si scheme HTTPS)
- ⚠️ Headers sécurité HTTP manquants sur les services FastAPI autres que PKI (CSP, HSTS, X-Frame-Options)
- ✅ CORS configurable (⚠️ défaut `"*"` — doit être restreint en production)

**Rétention des données :**
- ✅ `PROMETHEUS_RETENTION` configurable (défaut non précisé dans code)
- ✅ `LOKI_RETENTION` configurable
- ⚠️ Pas de politique de purge automatique des jobs AWX anciens (AWX dispose de cleanup tasks)
- ⚠️ Le rapport de conformité cache en `/tmp` — non persisté

---

## COUCHE 5 — DÉPLOIEMENT, SCALABILITÉ & RÉSILIENCE

### 5.1 Déploiement & Packaging

**Modes de déploiement supportés :**
- ✅ **Docker Compose** (mode principal) — `docker-compose.yml` complet avec 25+ services
- ✅ **Ubuntu 22.04/24.04** explicitement documenté
- ⚠️ **Pas de packaging OS** (pas de .deb, .rpm)
- ❌ **Pas de Helm chart ni d'opérateur Kubernetes**
- ❌ **Pas de déploiement bare-metal sans Docker**
- ❌ **Pas de support officiel Podman** (même si Receptor supporte Podman)

**Installation :**
- ✅ Deploy Wizard guide toute la configuration (<15 min documenté)
- ✅ Documentation MkDocs Material complète (Getting Started, Quick Start, etc.)
- ✅ Makefile avec cibles `start`, `stop`, `status`, `logs`, `wizard`
- ⚠️ Prérequis : `python3` + `sops` + `age-keygen` doivent être présents sur l'hôte
- ⚠️ Build de l'image AWX patché requis avant le premier déploiement (15+ min)

**Mises à jour :**
- ✅ Procédure documentée dans `docs/operations/updates.md`
- ⚠️ **Pas de zero-downtime upgrade** documenté ni implémenté
- ⚠️ La migration AWX vers une nouvelle version majeure nécessite un downtime (documenté dans ADR-001)
- ⚠️ L'`AWX_SECRET_KEY` ne peut jamais changer — dette technique majeure

**Opérateur Kubernetes :**
- ❌ **Absent** — le projet AWX dispose de l'AWX Operator, Autoflow n'en a pas

### 5.2 Scalabilité

**Architecture single-node assumée :**
> Citation directe de la doc : *"Pas une solution haute disponibilité (dans sa version actuelle) : c'est une architecture single-node."*

Limites de ressources définies dans `docker-compose.yml` :
| Service | CPU limit | RAM limit |
|---------|-----------|-----------|
| awx_web | 1.0 | 1 Go |
| awx_task | 2.0 | 2 Go |
| postgres | 1.0 | 512 Mo |
| redis | 0.5 | 256 Mo |
| gitea | 0.5 | 512 Mo |
| Monitoring stack | 0.5 | 512 Mo chacun |

**Scalabilité horizontale :**
- ❌ **Impossible en l'état** pour les services stateful (PostgreSQL, Redis, Gitea)
- ❌ **PKI stockée en JSON local** — non réplicable
- ❌ Aucun load balancing entre workers AWX multiples
- ⚠️ L'Event Engine peut theóriquement être scalé (Redis partagé) mais non testé ni documenté

**Estimé de capacité maximale (single-node) :**
- Jobs simultanés : ~10-20 (limité par les workers Celery awx_task, CPU 2.0/RAM 2Go)
- Hôtes gérés : dépend de la complexité des playbooks, théoriquement ~500-1000
- Pas de benchmark de charge dans le projet

**Goulots d'étranglement identifiés :**
1. `awx_task` : 2 CPU / 2 Go RAM — bottleneck principal
2. `postgres` unique pour AWX : point de défaillance unique
3. `gitea` single instance : pas de réplication native
4. PKI JSON file : dégradation linéaire en écriture

### 5.3 Résilience & Observabilité

**Gestion des pannes :**
- ✅ `restart: unless-stopped` sur tous les services critiques
- ✅ Healthchecks Docker sur tous les services (`pg_isready`, `redis-cli ping`, etc.)
- ✅ Volumes critiques `external: true` (protégés de `docker compose down -v`)
- ✅ Event Engine : fallback fire-and-forget si Redis indisponible
- ✅ PKI : fallback local si LDAP indisponible (configurable)
- ✅ Deploy Wizard : messages d'avertissement si services non healthy
- ⚠️ Pas de circuit breaker entre Autoflow API et AWX
- ⚠️ Si AWX est down, l'Event Engine met les événements en DLQ après 5 tentatives — mais pas de reprise automatique à la remontée d'AWX (polling continu en retry)

**Health checks :**
- ✅ `GET /health` sur API, Event Engine, PKI, Security Scanner
- ✅ `GET /health/ready` et `GET /health/awx` sur l'API
- ✅ Liveness/readiness Docker Healthcheck sur tous les services
- ⚠️ Pas d'endpoint de santé agrégé (type "health-of-the-stack")

**Monitoring et alertes :**
- ✅ Stack LGTM complète (Loki, Grafana, Tempo, Prometheus)
- ✅ Alertes Prometheus préconfigurées (services down, CPU/RAM/disk élevés, deadlocks PG, Redis rejeté)
- ✅ Alertes Alertmanager configurables (Slack, webhook)
- ✅ Tracing distribué OpenTelemetry sur API, Event Engine, PKI, Security Scanner
- ✅ Dashboards Grafana provisionnés (AWX, Gitea, Node, PKI, Security, Compliance)
- ✅ Métriques PKI Prometheus (certificats expiration, émissions, révocations)
- ✅ Métriques AWX exposées via `/api/v2/metrics/` d'AWX (collectées par Prometheus)

**Sauvegardes :**
- ✅ Restic avec politique GFS (Grandfather-Father-Son)
- ✅ `scripts/backup.sh` et `scripts/restore.sh`
- ✅ Dump PostgreSQL AWX inclus dans la sauvegarde
- ⚠️ Non automatisé par défaut (manque un cronjob dans la stack)

---

## COUCHE 6 — INTERFACE UTILISATEUR & EXPÉRIENCE DÉVELOPPEUR

### 6.1 Interface Web (UI)

**Autoflow dispose de plusieurs UIs :**

1. **AWX UI** (interface principale d'automatisation) : Complète, React, mature, fonctionnalité identique à AWX standalone
2. **Gitea UI** : Complète, gestion des repos, CI/CD, registry, issues
3. **Grafana UI** : Monitoring, logs, traces, alertes
4. **PKI UI** (`services/pki/app/templates/index.html`) : Interface de gestion des certificats
5. **Deploy Wizard UI** (`services/deploy-wizard/static/index.html`) : ~3600 lignes de HTML/JS/CSS inline

**Comparaison fonctionnelle avec AWX/AAP :**

| Fonctionnalité UI | Autoflow (AWX) | Autoflow Custom | AWX standalone | AAP |
|-------------------|---------------|----------------|----------------|-----|
| Dashboard jobs | ✅ AWX | ✅ Conformité | ✅ | ✅ |
| Inventaires | ✅ AWX | ❌ | ✅ | ✅ |
| Job Templates | ✅ AWX | ❌ | ✅ | ✅ |
| Credentials | ✅ AWX | ❌ | ✅ | ✅ |
| Schedules | ✅ AWX | ❌ | ✅ | ✅ |
| Workflows | ✅ AWX | ❌ | ✅ | ✅ |
| Projets SCM | ✅ AWX | ❌ | ✅ | ✅ |
| RBAC / Orgs / Teams | ✅ AWX | ❌ | ✅ | ✅ |
| Activity Stream | ✅ AWX | ❌ | ✅ | ✅ |
| Notifications AWX | ✅ AWX | ✅ + Slack/webhook | ✅ | ✅ |
| EE Management | ✅ AWX | ✅ Wizard Build/Rollback | ✅ | ✅ |
| PKI / Certificats | ❌ | ✅ UI dédiée | ❌ | ❌ |
| Monitoring | ❌ | ✅ Grafana | ❌ | ⚠️ |
| CVE Scan | ❌ | ✅ Trivy | ❌ | ⚠️ |
| Deploy guidé | ❌ | ✅ Wizard | ❌ | ⚠️ |
| Webhooks visuel | ❌ | ✅ Event Engine admin | ❌ | ⚠️ |
| Air-gap support | ❌ | ✅ EE local + Gitea | ⚠️ | ✅ |

**Évaluation UX du Deploy Wizard :**
- ✅ Server-Sent Events pour le streaming en temps réel (déploiement, build EE, PKI)
- ✅ Indicateurs de progression visuels
- ✅ Warnings contextuels (FIRST_START_ONLY, domaine changé, etc.)
- ✅ Interface responsive (CSS inline)
- ✅ Timeout de session inactif (30 min)
- ⚠️ Interface très fonctionnelle mais design basique (pas de design system reconnu)
- ⚠️ Formulaire unique très long (~10 sections) — UX pouvant décourager pour les 1ers déploiements

**Fonctionnalités critiques absentes de l'UI Autoflow :**
- ❌ Pas d'UI pour gérer les inventaires AWX (redirection vers UI AWX)
- ❌ Pas de vue unifiée de la santé de la stack (tableau de bord global)
- ❌ Pas de gestion des utilisateurs AWX depuis l'UI Autoflow

### 6.2 API & Intégrations

**Autoflow API (FastAPI, port 8000) :**

Endpoints disponibles :
```
GET  /health, /health/ready, /health/awx
POST /auth/token, GET /auth/me, POST /auth/refresh
GET  /awx/job-templates, /awx/job-templates/{id}
POST /awx/job-templates/{id}/launch
GET  /awx/jobs, /awx/jobs/{id}
GET  /awx/inventories, /awx/projects
GET  /awx/jobs/history, /awx/jobs/stats
POST /awx/jobs/{id}/watch
GET  /compliance/report, /compliance/report/latest
POST /compliance/report/generate
GET  /compliance/export/jobs.csv
GET  /compliance/score
GET  /metrics (Prometheus)
```

- ✅ OpenAPI/Swagger intégré (`/docs`, `/redoc`)
- ⚠️ API partielle : proxy de quelques endpoints AWX seulement (job templates, jobs, inventories, projects). Le reste de l'API AWX (workflows, credentials, organizations, teams, etc.) n'est pas exposé — il faut accéder directement à AWX.
- ⚠️ **Pas de versionnage** d'API (pas de `/api/v1/` ou `/api/v2/`)
- ⚠️ Pas de SDK client fourni

**Compatibilité API AWX :**
- L'Autoflow API est un **proxy additionnel** — les clients peuvent accéder directement à `awx.<DOMAIN>/api/v2/` pour les fonctionnalités non couvertes
- Migration depuis API AWX : compatible car la même API AWX est derrière

**Webhooks :**
- ✅ Entrants : GitHub, Alertmanager, générique (Event Engine)
- ✅ Sortants : notification webhook + Slack Block Kit au completion de job
- ⚠️ Pas de gestion des webhooks sortants depuis l'UI (configuration via `.env`)

### 6.3 Documentation & Onboarding

**Qualité de la documentation :**
- ✅ MkDocs Material complet (site/ généré)
- ✅ Sections : Overview, Getting Started, Configuration, Operations, Security, Runbook, API Reference
- ✅ ADR (Architecture Decision Records) — pratique rare et appréciée
- ✅ Runbooks pour disaster recovery, incident response, troubleshooting
- ✅ Guide de démarrage rapide fonctionnel (<15 min)
- ✅ Documentation de la PKI avec LDAP, gestion des clés
- ⚠️ Pas de guide de migration depuis AWX standalone vers Autoflow
- ⚠️ Pas de guide de migration depuis AAP vers Autoflow
- ⚠️ Documentation majoritairement en **français** — frein pour l'adoption internationale
- ⚠️ Pas de documentation des cas d'usage enterprise avancés (multi-org, HA, audit RGPD)

---

## COUCHE 7 — TESTS & FIABILITÉ

### 7.1 Couverture de tests

**Inventaire des tests :**

```
tests/
├── unit/
│   ├── test_rules.py      (178 lignes) — Rule engine complet ✅
│   ├── test_dedup.py      (177 lignes) — Déduplication Redis/memory ✅
│   ├── test_parsers.py    (139 lignes) — Parseurs GitHub/Alertmanager ✅
│   └── wizard/
│       ├── test_constants.py   — Constantes wizard
│       └── test_env.py         — Gestion .env
├── integration/
│   ├── test_event_flow.py     (385 lignes) — Flux événements complet ✅
│   ├── test_api_notification.py (284 lignes) — Notifications API ✅
│   └── wizard/
│       ├── test_config.py      — Config wizard
│       ├── test_generate.py    — Génération secrets
│       ├── test_preflight.py   — Vérifications pré-déploiement
│       └── test_schema.py      — Schéma de configuration
└── e2e/
    └── test_stack.py      (221 lignes) — Stack complète avec stubs AWX/callback ✅
```

**Couverture estimée :**
| Module | Tests | Couverture estimée |
|--------|-------|-------------------|
| Event Engine (rules, dedup, parsers) | ✅ Bons | ~75% |
| Event Engine (main, retry, scheduler) | ⚠️ Partiel | ~40% |
| Autoflow API (auth, awx, compliance) | ⚠️ Via intégration | ~35% |
| PKI Service | ❌ Aucun | ~0% |
| Security Scanner | ❌ Aucun | ~0% |
| Deploy Wizard | ⚠️ Partiel (wizard/) | ~20% |
| EE Builder | ❌ Aucun | ~0% |

**Zones sans tests représentant un risque élevé :**
- ❌ **PKI Service** : gestion de certificats X.509, CRL, LDAP — aucun test malgré la criticité
- ❌ **Security Scanner** : rapports de conformité Trivy — aucun test
- ❌ **EE Builder** : build Execution Environments — aucun test
- ⚠️ **Deploy Wizard** : seul le schéma et la config sont testés — les endpoints SSE critiques (déploiement, PKI, build EE) ne sont pas testés

### 7.2 CI/CD & Qualité continue

**Pipeline CI/CD :**
- `.github/workflows/docs.yml` : déploiement MkDocs sur GitHub Pages (push sur main/devel)
- `.gitea/workflows/docs.yml` : même pipeline pour Gitea
- `execution-environments/.gitea/workflows/ee-build.yml` : build EE sur Gitea Actions
- `playbooks/network/.gitea/workflows/ci.yml` : tests des playbooks réseau

**Gates de qualité :**
- ❌ **Pas de CI pour les tests Python** (unit/integration/e2e non exécutés automatiquement)
- ❌ **Pas de linting automatique** (flake8/ruff/pylint)
- ❌ **Pas de scan SAST** (bandit, semgrep)
- ❌ **Pas de coverage minimum** enforced
- ⚠️ `Makefile` contient `make test` et `make test-e2e` mais non intégrés au CI

**Versionnement :**
- ✅ Semantic versioning déclaré (`1.0.0` en changelog)
- ✅ Changelog `docs/changelog/index.md` existant
- ⚠️ Pas de changelog automatique (conventional commits, towncrier, etc.)
- ⚠️ Pas de tag git visible dans le projet (version déclarée manuellement)

---

## COUCHE 8 — ANALYSE CONCURRENTIELLE & POSITIONNEMENT MARCHÉ

### 8.1 Matrice de comparaison fonctionnelle

| Fonctionnalité | **Autoflow** | AWX (open-source) | Ansible AAP (RedHat) | Semaphore UI | Rundeck |
|---|---|---|---|---|---|
| Exécution de playbooks | ✅ (via AWX) | ✅ | ✅ | ✅ | ⚠️ (plugin) |
| Inventaires dynamiques | ✅ (via AWX) | ✅ | ✅ | ⚠️ | ✅ |
| RBAC | ✅ AWX + ⚠️ API mono-user | ✅ | ✅ | ⚠️ | ✅ |
| Workflows conditionnels | ✅ (via AWX) | ✅ | ✅ | ❌ | ✅ |
| Execution Environments | ✅ + Build intégré | ✅ | ✅ | ❌ | ❌ |
| HA / Clustering | ❌ Single-node | ✅ | ✅ | ❌ | ✅ |
| API REST complète | ⚠️ Proxy partiel + AWX | ✅ | ✅ | ⚠️ | ✅ |
| Webhooks / CI-CD | ✅ Event Engine (GitHub, Alertmanager) | ⚠️ | ✅ | ✅ | ✅ |
| Notifications | ✅ Slack + webhook | ✅ | ✅ | ⚠️ | ✅ |
| Multi-tenancy | ✅ via AWX (Organisations) | ⚠️ | ✅ | ❌ | ✅ |
| Audit trail | ✅ AWX Activity Stream + API log | ✅ | ✅ | ❌ | ✅ |
| SSO/SAML/LDAP | ✅ AWX + ⚠️ PKI uniquement | ✅ AWX | ✅ | ❌ | ✅ |
| Kubernetes natif | ❌ | ✅ AWX Operator | ✅ | ❌ | ⚠️ |
| PKI interne | ✅ **UNIQUE** | ❌ | ❌ | ❌ | ❌ |
| Monitoring intégré | ✅ **LGTM complet** | ❌ | ⚠️ | ❌ | ⚠️ |
| CVE Scanning | ✅ **Trivy intégré** | ❌ | ⚠️ | ❌ | ❌ |
| Git + CI/CD intégré | ✅ **Gitea + Act Runner** | ❌ | ⚠️ | ❌ | ❌ |
| Sauvegarde intégrée | ✅ **Restic GFS** | ❌ | ⚠️ | ❌ | ❌ |
| Air-gap ready | ✅ **Complet** | ⚠️ | ✅ | ❌ | ⚠️ |
| Installation guidée (<15 min) | ✅ **Deploy Wizard** | ❌ | ❌ | ✅ | ⚠️ |
| Licence | **⚠️ Non déclarée** | Apache 2.0 | Propriétaire | MIT | Apache/Comm. |
| Documentation | ✅ MkDocs complet | ✅ | ✅ | ⚠️ | ✅ |

### 8.2 Différenciateurs & Proposition de valeur

**Ce qu'Autoflow fait MIEUX ou DIFFÉREMMENT :**

1. **Déploiement intégré clé en main** : C'est le différenciateur N°1. Aucun concurrent ne propose un assistant de déploiement graphique générant automatiquement tous les secrets, certificats TLS, et déployant la stack complète en moins de 15 minutes.

2. **PKI interne avec LDAP/AD et auto-renouvellement** : Unique dans l'écosystème Ansible. Gérer ses propres certificats (sans Let's Encrypt, sans dépendance externe) pour une stack interne est un besoin réel en enterprise/air-gap.

3. **Stack LGTM full-observability out-of-the-box** : AWX standalone n'inclut aucun monitoring. Autoflow livre Prometheus, Grafana, Loki, Tempo, Alertmanager, Node Exporter préconfigurés avec des dashboards et des règles d'alerte.

4. **Event Engine robuste avec DLQ** : Un moteur d'événements avec Redis, retry exponentiel, dead-letter queue, hot-reload des règles — plus robuste que de simples webhooks AWX.

5. **Air-gap ready de bout en bout** : Build local des EE, registry Gitea interne, collections pré-installées dans les images — un scénario souvent douloureux rendu trivial.

6. **CVE scanning intégré** : Trivy scanne automatiquement toutes les images de la stack et génère des rapports de conformité.

**Proposition de valeur :**
> *"AWX en production en moins d'une heure, avec Git, monitoring, PKI, sauvegardes et sécurité inclus."*

**Segments de marché adressables AUJOURD'HUI :**
- ✅ **MSP / Intégrateurs système** : déploiement reproductible chez chaque client, onboarding rapide
- ✅ **PME avec équipe DevOps réduite** (1-5 personnes) : plateforme complète sans expertise multiple
- ✅ **Laboratoires de formation** : réaliste, complet, déployable en minutes
- ✅ **Environnements air-gapped** (industriel, défense, banque) : complètement autonome
- ⚠️ **Startups / scale-up DevOps** : si single-node accepté à court terme
- ❌ **Grandes entreprises (>1000 hôtes)** : bloqué par le single-node

### 8.3 Points bloquants pour la mise sur le marché

#### 🔴 BLOQUANT — Empêche la vente ou crée un risque légal

1. **Absence de licence** : Aucun fichier `LICENSE` dans le projet. Sans licence explicite, le code est techniquement "tous droits réservés" — les clients ne peuvent pas légalement l'utiliser ni le redistribuer. **Impact : zéro vente possible légalement.** Effort : 0.5j — choisir une licence (MIT, Apache 2.0, BSL, etc.)

2. **Grafana/Loki/Tempo AGPL-3.0** : Si Autoflow est vendu comme un service managé (SaaS) ou redistribué avec ces composants, la licence AGPL exige la publication du code source de l'ensemble. À clarifier juridiquement avant tout engagement commercial. **Impact : modèle commercial limité sans avis juridique.**

#### 🟠 CRITIQUE — Fera fuir les prospects enterprise au premier POC

3. **Aucune haute disponibilité** : Un prospect enterprise qui a des SLAs à respecter éliminera immédiatement la solution. Single-node = SPOF. **Impact : bloque les grands comptes.** Effort : 20-40j/h pour une migration vers Docker Swarm ou Kubernetes.

4. **Autoflow API mono-utilisateur** : L'API custom d'Autoflow ne supporte qu'un seul utilisateur (admin). Dans un contexte enterprise avec plusieurs équipes, chaque équipe doit utiliser l'API AWX directement — ce qui contourne la couche d'abstraction Autoflow. **Effort : 5-10j/h pour ajouter un RBAC multi-user.**

5. **Pas de CI sur les tests Python** : Impossible de garantir la non-régression sans CI automatisé. Un bug en production après une mise à jour détruira la confiance client. **Effort : 2-3j/h.**

6. **Zéro tests sur PKI, Security Scanner, EE Builder** : Ces composants sont critiques pour la sécurité et ne disposent d'aucun test automatisé. **Effort : 10-15j/h.**

7. **HTTP Security Headers incomplets** : Absence de CSP, HSTS, X-Frame-Options sur les services FastAPI (API, Event Engine, Security Scanner). Un audit sécurité basique les détectera. **Effort : 1-2j/h.**

#### 🟡 IMPORTANT — Handicapera la compétitivité

8. **Documentation en français** : Pour toucher un marché international ou les équipes non-francophones, la documentation doit être en anglais. **Effort : 5-10j/h de traduction.**

9. **Pas de support Kubernetes** : Les équipes cloud-native n'utilisent pas Docker Compose en production. L'absence de Helm chart ou d'opérateur limite fortement l'adoption. **Effort : 30-60j/h.**

10. **Pas de zero-downtime update** : Toute mise à jour nécessite un arrêt complet. En production, c'est inacceptable pour beaucoup d'entreprises. **Effort : 10-20j/h.**

11. **Versionnage API non implémenté** : Sans `/api/v1/`, les breaking changes casseront les intégrations clients. **Effort : 3-5j/h.**

12. **Secrets AWS/Azure/Vault non intégrés** : Les enterprise utilisent Vault, KMS, Azure Key Vault. Sans connecteur, les secrets restent dans `.env`. AWX supporte ces backends mais non exposés dans le wizard. **Effort : 10-15j/h.**

13. **Deploy Wizard : fichier 3600+ lignes** : Maintenabilité rédhibitoire pour onboarder des contributeurs ou faire évoluer l'UI. **Effort : 10-15j/h de refactoring.**

#### 🟢 NICE-TO-HAVE — Enrichissement à moyen terme

14. Multi-tenancy au niveau Autoflow (isolation entre clients d'un même MSP)
15. SDK client Python/CLI pour l'Autoflow API
16. Intégration Terraform/Pulumi pour le provisioning des ressources avant les playbooks
17. Interface mobile-responsive pour les dashboards
18. Support Windows pour l'installation (WSL2 documentation)
19. Rotation automatique des tokens API
20. MFA pour le Deploy Wizard et l'Autoflow API

---

## COUCHE 9 — SYNTHÈSE & FEUILLE DE ROUTE

### 9.1 Verdict de maturité

| Axe | Note | Justification |
|-----|------|---------------|
| **Complétude fonctionnelle** | 7/10 | Excellent périmètre via AWX + services custom. Manque HA et API complète |
| **Stabilité & fiabilité** | 6/10 | Architecture saine, volumes protégés, mais single-node et tests insuffisants sur composants critiques |
| **Sécurité** | 6/10 | PKI solide, SOPS, audit. Manque headers HTTP, MFA, révocation JWT, licence |
| **Scalabilité** | 2/10 | Explicitement single-node. Bloquant pour enterprise |
| **Expérience utilisateur** | 7/10 | Deploy Wizard excellent, AWX UI complète, Grafana intégré. UX du wizard perfectible |
| **Documentation** | 8/10 | MkDocs complet, ADR, runbooks — rare pour un projet à ce stade |
| **Testabilité** | 4/10 | Bons tests Event Engine, néant sur PKI/Scanner/Wizard/EE Builder |
| **Prêt pour l'enterprise** | 4/10 | Single-node + mono-user API + pas de licence = non-starter pour les grands comptes |
| **Score global de maturité marché** | **5.5/10** | Solution solide pour PME/MSP/labs mais non prête pour enterprise moyen-grand |

### 9.2 Recommandation finale

## ⚠️ OUI, mais dans 4 à 8 semaines — après corrections précises

La solution est fonctionnellement riche et techniquement solide pour son périmètre cible (PME, MSP, labs, air-gap). Elle peut être commercialisée **immédiatement pour les PME et MSP** après resolution des bloquants légaux.

**Pour un lancement minimal viable MAINTENANT :**
1. Ajouter une licence (0.5 jours)
2. Clarifier la situation AGPL avec un conseil juridique
3. Documenter explicitement "single-node = non-HA" dans les conditions de vente

**Pour un lancement solide (4-8 semaines) :**
1. Ajouter CI + tests manquants
2. Sécuriser les HTTP headers
3. Traduire la documentation critique en anglais
4. Versionner l'API

**Ce que la solution ne peut PAS adresser sans refonte architecturale :**
- Enterprise avec SLA (nécessite HA)
- Déploiement Kubernetes natif (nécessite Helm/Operator)
- Multi-tenancy à l'échelle (nécessite refonte RBAC)

### 9.3 Feuille de route priorisée

---

### Sprint 0 — Bloquants absolus (avant tout lancement) [~1 semaine]

- [ ] **Licence** : Ajouter un fichier `LICENSE` (Apache 2.0 recommandé pour la compatibilité AWX). **Impact : légal, requis avant toute vente. Effort : 0.5j.**
- [ ] **Clarification AGPL** : Obtenir un avis juridique sur la redistribution des composants AGPL (Grafana, Loki, Tempo, MinIO). Documenter le modèle d'usage autorisé. **Impact : risque légal. Effort : 2-3j (juridique).**
- [ ] **HTTP Security Headers** : Ajouter un middleware `SecurityHeadersMiddleware` sur Autoflow API, Event Engine, Security Scanner (copier le pattern de PKI). **Impact : sécurité, audit. Effort : 1j.**
- [ ] **CI Python minimal** : Configurer GitHub Actions pour exécuter les tests unit/integration sur PR. **Impact : qualité, confiance. Effort : 1-2j.**
- [ ] **CORS restricté par défaut** : Changer `cors_origins: str = "*"` en `"*"` uniquement en mode développement explicite. **Impact : sécurité. Effort : 0.5j.**

---

### Phase 1 — MVP commercial (J+15 à J+45)

- [ ] **Tests PKI** : Écrire des tests unitaires et d'intégration pour le service PKI (issue/revoke/renew/CRL, auth LDAP mockée). **Impact : fiabilité composant critique. Effort : 8-10j/h.**
- [ ] **Autoflow API multi-user** : Refactorer l'auth API pour supporter plusieurs utilisateurs avec rôles (admin/operator/viewer), table d'utilisateurs (JSON ou SQLite). **Impact : enterprise readiness. Effort : 5-7j/h.**
- [ ] **Versionnage API** : Préfixer les routes Autoflow API sous `/api/v1/`. **Impact : compatibilité long-terme. Effort : 2-3j/h.**
- [ ] **Refactoring Deploy Wizard** : Découper `main.py` (3600+ lignes) en modules : `pki.py`, `deployment.py`, `ee.py`, `preflight.py`. **Impact : maintenabilité. Effort : 5-8j/h.**
- [ ] **Documentation en anglais** : Traduire au minimum Getting Started, Architecture, Quick Start, API Reference. **Impact : adoption internationale. Effort : 5-8j/h.**
- [ ] **Révocation JWT** : Implémenter une liste noire Redis pour les tokens invalidés. **Impact : sécurité. Effort : 2-3j/h.**
- [ ] **Zero-downtime update pour les services custom** : Script de rolling update pour API, Event Engine (hors AWX/PG). **Impact : production-readiness. Effort : 3-5j/h.**

---

### Phase 2 — Compétitivité enterprise (J+45 à J+120)

- [ ] **Tests Security Scanner & EE Builder** : Couverture tests pour les scanners Trivy et le builder EE. **Impact : fiabilité. Effort : 8-10j/h.**
- [ ] **MFA Deploy Wizard** : Ajouter TOTP (Google Authenticator compatible) au wizard. **Impact : sécurité enterprise. Effort : 3-5j/h.**
- [ ] **Intégration Vault/KMS** : Connecteur HashiCorp Vault pour les secrets (via AWX credential plugins existants + configuration wizard). **Impact : enterprise. Effort : 10-15j/h.**
- [ ] **Health dashboard agrégé** : Endpoint API `/health/full` + page Grafana agrégeant la santé de tous les services. **Impact : opérabilité. Effort : 3-5j/h.**
- [ ] **Sauvegarde automatisée** : Cronjob Restic intégré dans le docker-compose (container cron). **Impact : fiabilité opérations. Effort : 2-3j/h.**
- [ ] **Linting + pre-commit hooks** : Configurer ruff, mypy, pre-commit. **Impact : qualité code. Effort : 2-3j/h.**
- [ ] **SDK CLI Python** : Client Python/CLI `autoflow-cli` wrappant l'API pour les intégrations CI/CD. **Impact : adoption DevOps. Effort : 10-15j/h.**
- [ ] **Support multi-domaines Gitea** : Permettre de pointer les projets AWX vers un SCM externe (GitHub/GitLab Enterprise) via le wizard. **Impact : adoption entreprises ayant un SCM existant. Effort : 5-8j/h.**

---

### Phase 3 — Différenciation & croissance (J+120+)

- [ ] **Haute Disponibilité** : Migration de l'architecture vers Docker Swarm ou Kubernetes avec bases de données répliquées. Prérequis : refonte de la PKI JSON vers PostgreSQL. **Impact : grands comptes. Effort : 40-80j/h.**
- [ ] **Helm Chart / Kubernetes** : Packaging Helm pour déploiement K8s. **Impact : adoption cloud-native. Effort : 20-30j/h.**
- [ ] **Multi-tenancy platform** : Isolation entre tenants au niveau Autoflow (organisations distinctes, namespaces Docker ou K8s). **Impact : MSP avec plusieurs clients sur une même instance. Effort : 30-50j/h.**
- [ ] **Marketplace d'Event Engine rules** : Bibliothèque de règles préconfigurées pour intégrations courantes (GitLab, Azure DevOps, PagerDuty, Datadog). **Impact : time-to-value client. Effort : 10-15j/h.**
- [ ] **SIEM integration** : Export des logs audit vers Splunk/Elastic SIEM. **Impact : compliance enterprise. Effort : 5-10j/h.**
- [ ] **Reporting conformité avancé** : Rapports NIST, CIS, ISO 27001, PCI-DSS basés sur les jobs AWX + CVE Trivy + PKI expiration. **Impact : régulé/banque/défense. Effort : 20-30j/h.**

---

*Analyse réalisée par Claude Code le 2026-05-23 — Version 1.0*
