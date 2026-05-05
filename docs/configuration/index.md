---
title: Configuration
---

# Configuration

Référence complète de la configuration Autoflow. Chaque variable du fichier `.env` est documentée avec son type, sa valeur par défaut, son impact et des exemples.

| Page | Contenu |
|---|---|
| [Variables d'environnement](environment-variables.md) | Toutes les variables `.env` — référence exhaustive |
| [Réseau & Traefik](network-topology.md) | Ports, routing, sous-domaines, headers |
| [TLS & PKI](tls-pki.md) | CA interne, certificats, trust |
| [AWX](awx.md) | Settings.py, EE, tokens, ALLOWED_HOSTS |
| [Gitea](gitea.md) | Registry, runners, webhooks, Actions |
| [Execution Environments](execution-environments.md) | Build, push, intégration AWX |
| [Event Engine](event-engine.md) | Rules, schedules, notifications |
| [Monitoring](monitoring.md) | Prometheus, Grafana, Loki, alertes |
| [Backup & DR](backup.md) | Restic, backends, GFS, cron |
| [Security Scanner](security-scanner.md) | Trivy, CVE, rapports conformité |
| [Air-gapped](air-gapped.md) | Déploiement sans internet |

!!! tip "Point de départ"
    La page [Variables d'environnement](environment-variables.md) est la référence principale. Les autres pages approfondissent chaque composant.
