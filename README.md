# Autoflow

**Plateforme d'automatisation entreprise** — auto-hébergée, air-gap ready, déployable en 15 minutes.

📖 **[Documentation complète](https://teachmemore-ressources.github.io/autoflow/)**

---

## Qu'est-ce qu'Autoflow ?

Autoflow assemble en une stack cohérente les meilleurs outils open-source d'automatisation :

| Composant | Rôle |
|---|---|
| **AWX** | Moteur d'orchestration Ansible — RBAC, jobs, inventaires, EE |
| **Gitea** | Dépôts Git + container registry + CI/CD (Gitea Actions) |
| **Event Engine** | Déclenchement de jobs sur webhooks (Gitea, GitHub, Alertmanager) |
| **Traefik** | Reverse proxy TLS — point d'entrée unique |
| **PKI interne** | Autorité de certification — certificats TLS sans Let's Encrypt |
| **Prometheus + Grafana** | Monitoring, alertes, dashboards |
| **Loki + Promtail** | Centralisation des logs |
| **Restic** | Sauvegardes chiffrées incrémentales |
| **Deploy Wizard** | Interface web de configuration et déploiement guidé |

---

## Démarrage rapide

### Prérequis

- Ubuntu 22.04 / 24.04
- Docker ≥ 24, Docker Compose v2
- 4 CPU, 8 GB RAM, 40 GB disque
- Un domaine DNS pointant vers le serveur

### Installation

```bash
# 1. Cloner le dépôt
git clone https://github.com/Teachmemore-Ressources/autoflow.git
cd autoflow

# 2. Lancer le Deploy Wizard
export WIZARD_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
make wizard
# → http://localhost:9000  (tunnel SSH si serveur distant)
ssh -L 9000:localhost:9000 user@<IP_SERVEUR>
```

Le wizard guide à travers la configuration, génère tous les secrets, crée les certificats TLS et déploie la stack.

> Guide complet → [Getting Started](https://teachmemore-ressources.github.io/autoflow/getting-started/)

### Redémarrer la stack

```bash
make start    # démarre (crée les volumes critiques si nécessaire)
make stop     # arrête (données préservées)
make status   # état des containers
make logs     # logs en temps réel
```

---

## Accès aux services

| Service | URL |
|---|---|
| AWX | `https://awx.<DOMAIN>` |
| Gitea | `https://git.<DOMAIN>` |
| Grafana | `https://monitoring.<DOMAIN>` |
| PKI | `https://pki.<DOMAIN>` |
| Deploy Wizard | `https://wizard.<DOMAIN>` |

---

## Documentation

📖 [https://teachmemore-ressources.github.io/autoflow/](https://teachmemore-ressources.github.io/autoflow/)

Sections disponibles :

- [Vue d'ensemble & Architecture](https://teachmemore-ressources.github.io/autoflow/overview/)
- [Guide de démarrage](https://teachmemore-ressources.github.io/autoflow/getting-started/)
- [Référence de configuration](https://teachmemore-ressources.github.io/autoflow/configuration/)
- [Opérations (Day-2)](https://teachmemore-ressources.github.io/autoflow/operations/)
- [Sécurité](https://teachmemore-ressources.github.io/autoflow/security/)
- [Runbook & Dépannage](https://teachmemore-ressources.github.io/autoflow/runbook/)
- [Référence API](https://teachmemore-ressources.github.io/autoflow/api-reference/)

Pour lire la documentation localement avant de déployer :

```bash
pip install -r docs/requirements.txt
mkdocs serve
# → http://localhost:8000
```
