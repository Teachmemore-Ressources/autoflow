---
title: Développement
---

# Guide de développement

Cette section s'adresse aux contributeurs et aux équipes qui souhaitent étendre Autoflow : ajouter des playbooks, construire des Execution Environments personnalisés, intégrer de nouveaux services, ou comprendre les choix d'architecture.

---

## Contenu

| Page | Description |
|---|---|
| [Environnement local](local-dev.md) | Setup de développement sur sa machine |
| [Construire un EE](building-ee.md) | Guide complet ansible-builder |
| [Écrire des playbooks](writing-playbooks.md) | Conventions et bonnes pratiques |
| [Ajouter un service](adding-services.md) | Intégrer un nouveau service Docker |
| [ADR](adr/index.md) | Architecture Decision Records |

---

## Prérequis développement

```bash
# Outils nécessaires
python3 --version        # >= 3.11
ansible --version        # >= 2.15
ansible-builder --version # >= 3.0
docker --version         # >= 24
git --version

# Installer les outils Python de développement
pip install ansible ansible-builder ansible-lint yamllint pre-commit
```

---

## Structure du projet

```
autoflow/
├── docker-compose.yml          # Orchestration des services
├── .env                        # Variables d'environnement (non versionné)
├── .env.enc                    # .env chiffré avec SOPS (versionné)
├── .sops.yaml                  # Configuration SOPS/Age
├── awx/
│   ├── settings.py             # Configuration Django AWX
│   └── execution-environments/ # Définitions EE ansible-builder
├── services/
│   ├── deploy-wizard/          # Application FastAPI du wizard
│   ├── event-engine/           # Event Engine FastAPI
│   ├── pki/                    # Service PKI interne
│   └── security-scanner/       # Scanner de vulnérabilités
├── traefik/
│   ├── traefik.yml             # Config statique Traefik
│   └── dynamic/                # Config dynamique (TLS, middlewares, routes)
├── prometheus/
│   ├── prometheus.yml          # Config scraping
│   └── rules/                  # Règles d'alerte
├── grafana/
│   ├── provisioning/           # Datasources et dashboards provisionnés
│   └── dashboards/             # Fichiers JSON des dashboards
├── loki/                       # Config Loki
├── promtail/                   # Config Promtail
└── docs/                       # Cette documentation (MkDocs)
```

---

## Workflow de contribution

```bash
# 1. Créer une branche de feature
git checkout -b feature/mon-improvement

# 2. Développer et tester
# ...

# 3. Vérifier la syntaxe des playbooks
ansible-lint playbooks/

# 4. Vérifier les fichiers YAML
yamllint .

# 5. Commit et push
git add -p
git commit -m "feat: description de la feature"
git push origin feature/mon-improvement

# 6. Ouvrir une PR dans Gitea
```
