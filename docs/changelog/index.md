---
title: Changelog
---

# Changelog

Autoflow suit [Semantic Versioning](https://semver.org/). Les changements sont documentés ici.

---

## Format

```
## [MAJOR.MINOR.PATCH] — YYYY-MM-DD

### Ajouté
### Modifié
### Corrigé
### Supprimé
### Sécurité
```

---

## [Unreleased]

### Ajouté
- Documentation MkDocs Material complète (toutes les sections)
- Runbook : [Dépannage Proxmox](runbook/troubleshooting/proxmox.md) — nouvelle page dédiée : permissions API token (SDN.Use, DatastoreAdmin), plugin inventaire `community.proxmox.proxmox`, credential injectors dual env+extra_vars, kvm bool→string Jinja2, tâche qmstart périmée (`since=` filter)
- Champ `AWX_ALLOWED_HOSTS` configurable depuis le Deploy Wizard
- Endpoint SSE `/api/preflight/ee-dns/stream` pour le diagnostic DNS EE en temps réel
- Healthcheck bash `/dev/tcp` pour Promtail (remplace wget absent)
- Désactivation du healthcheck OTel Collector (image distroless)
- Patch AWX `inventory_yaml_detection` : support des fichiers `.yml`/`.yaml` dans le dropdown "Inventory file" (plugin inventory et inventaires statiques YAML) — appliqué au build via `Dockerfile.patched` et `community/config/awx/Dockerfile`
- Version Community : support du cert CA auto-signé via `CA_CERT_PATH` dans `.env` (variable `AWX_ISOLATION_SHOW_PATHS` et `DEFAULT_CONTAINER_RUN_OPTIONS` construites dynamiquement dans `community/config/awx/settings.py`)
- Runbook : [Problème 16 — Fichiers YAML absents du dropdown Inventory file](runbook/troubleshooting/awx.md)
- Runbook : [Problème 6b — `error setting certificate file` + ghost directory](runbook/troubleshooting/certificates.md)
- Configuration AWX : [section Types de credentials personnalisés](configuration/awx.md#types-de-credentials-personnalises) — définition complète du type `Proxmox API Token` (inputs, injectors, procédure de recréation UI + API, création token Proxmox côté hyperviseur, fichier d'inventaire associé)

### Modifié
- `awx/settings.py` : `ALLOWED_HOSTS` dérivé de `AWX_ALLOWED_HOSTS` env var
- Deploy Wizard : `deriveDomainFields()` inclut `AWX_ALLOWED_HOSTS`
- DNS EE check : séparation config-check rapide / test Docker en streaming
- `docker-compose.yml` : `TRAEFIK_CERTS_DIR` dans `awx_task` désormais dynamique (`${TRAEFIK_CERTS_DIR:-...}`) — était hardcodé, empêchant le bon montage du cert CA sur des serveurs avec un chemin différent
- `docker-compose.yml` : ajout de `DOMAIN` et `TRAEFIK_CERTS_DIR` dans l'environnement de `awx_web` (les valeurs `AWX_ISOLATION_SHOW_PATHS` et l'UI AWX affichaient le mauvais chemin de cert)
- `community/docker-compose.yml` : ajout de `CA_CERT_PATH` dans les environments `awx_web` et `awx_task`
- `community/.env.example` : documentation de `CA_CERT_PATH` avec exemple
- `.gitignore` : réécriture complète — ajout de `traefik/certs/*.crt`, `traefik/certs/*.key`, `.env.bak.*`, `community/.env`, `.cli-venv/`, `.wizard-venv/`, `__pycache__/`, `*.log`, `.claude/`

### Corrigé
- `kvm=0` ignoré par `proxmox_kvm` : Jinja2 rend `false` → chaîne `"False"` (truthy pour Proxmox). **Fix** : API REST avec `kvm: "{{ '1' if vm_kvm else '0' }}"` + vérification GET /config
- Tâche `qmstart` périmée retournée par `GET /tasks?typefilter=qmstart&limit=1` : l'ancienne tâche (run précédent) a déjà `endtime` renseigné, déclenchant `failed_when` dès l'attempt 1. **Fix** : ajout du filtre `since=<timestamp avant start>` pour n'exposer que les nouvelles tâches
- Encodage clé SSH Proxmox : `| urlencode` laisse `/` non encodé (RFC 3986), Proxmox rejette la valeur. **Fix** : `| trim | urlencode | replace('/', '%2F')`
- Service chrony : handler `Restart chronyd` utilisait toujours le nom `chronyd` (RHEL), échouait sur Debian où le service s'appelle `chrony`. **Fix** : nom conditionnel dans le handler
- Variables indéfinies dans `01_clone_vm.yml` (`vm_kvm`, `vm_cpu_type`, `vm_balloon`, etc.) : absentes de `group_vars/all.yml`, uniquement dans les role defaults non chargés. **Fix** : ajout dans `group_vars/all.yml`
- `vm_domain` / `vm_cidr` manquants dans `add_host` de `02_configure_vm.yml` et `03_validate_vm.yml` — rôle `vm_baseline` et rapport FQDN échouaient
- Cert CA disparu sur prod après `git pull` (les fichiers `.crt` étaient trackés en git, `git rm --cached` + commit → suppression physique par `git pull` sur le serveur distant). **Fix** : `.gitignore` + restauration depuis le volume `autoflow_pki_data`, redémarrage PKI pour re-sync automatique futur
- Ghost directory `ca.<DOMAIN>.crt` créé par Docker lorsque le fichier source est absent au démarrage du container — Docker crée un répertoire vide au lieu de monter un fichier, causant `error setting certificate file`. **Fix** : `rm -rf` du ghost directory + restauration du fichier + `--force-recreate` des containers AWX

---

## [1.0.0] — 2024-05-01

### Ajouté
- Stack complète Autoflow : AWX, Gitea, Traefik, Prometheus, Grafana, Loki
- Deploy Wizard (FastAPI) pour la configuration et le déploiement guidé
- Event Engine avec support webhooks Gitea, GitHub, Alertmanager
- PKI interne avec API REST
- Sauvegarde automatique Restic avec politique GFS
- Security Scanner (Trivy) avec rapports de conformité (NIST, CIS, ISO, PCI-DSS, SOC2)
- Support SOPS+Age pour le chiffrement du `.env`
- Gitea Actions runner intégré
- OpenTelemetry Collector + Jaeger pour le tracing distribué
- Dashboards Grafana provisionnés (AWX, Gitea, Traefik, Node Exporter, Loki)
- Alertes Prometheus préconfigurées (certificats, disque, services down)

### Services inclus
- AWX 24.x (awx_web, awx_task, awx_rsyslog)
- PostgreSQL 15 (AWX + PKI)
- Redis 7
- Gitea 1.22
- Traefik 3.x
- Prometheus 2.x
- Grafana 11.x
- Loki 3.x
- Promtail 3.x
- Alertmanager 0.27.x
- OpenTelemetry Collector
- Jaeger
- Restic (via script cron)
- Trivy (Security Scanner)
