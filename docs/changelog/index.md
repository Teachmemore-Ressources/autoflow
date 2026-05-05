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
- Champ `AWX_ALLOWED_HOSTS` configurable depuis le Deploy Wizard
- Endpoint SSE `/api/preflight/ee-dns/stream` pour le diagnostic DNS EE en temps réel
- Healthcheck bash `/dev/tcp` pour Promtail (remplace wget absent)
- Désactivation du healthcheck OTel Collector (image distroless)

### Modifié
- `awx/settings.py` : `ALLOWED_HOSTS` dérivé de `AWX_ALLOWED_HOSTS` env var
- Deploy Wizard : `deriveDomainFields()` inclut `AWX_ALLOWED_HOSTS`
- DNS EE check : séparation config-check rapide / test Docker en streaming

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
