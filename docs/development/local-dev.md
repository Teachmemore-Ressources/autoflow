---
title: Environnement local
---

# Environnement de développement local

Ce guide explique comment monter un environnement de développement Autoflow complet sur une machine locale ou une VM de dev.

---

## Option 1 — VM dédiée (recommandé)

Utiliser une VM Ubuntu 24.04 identique à la production. Avantages :

- Pas d'interférence avec votre machine hôte
- Docker en natif (performances optimales)
- Réseau isolé avec ses propres certificats TLS

```bash
# Provisionner avec Vagrant par exemple
vagrant init ubuntu/noble64
vagrant up
vagrant ssh
```

---

## Option 2 — Docker Desktop (Mac/Windows)

!!! warning "Limitations"
    Les certificats TLS internes et la résolution DNS peuvent nécessiter des configurations spécifiques. Les performances I/O sont réduites.

```bash
# Installer Docker Desktop depuis https://docs.docker.com/desktop/
# Activer WSL2 backend sur Windows
```

---

## Installation sur VM de dev

### 1. Prérequis

```bash
# Docker
curl -fsSL https://get.docker.com | sudo bash
sudo usermod -aG docker $USER
newgrp docker

# Outils
sudo apt install -y git make python3-pip age

# SOPS
SOPS_VERSION=$(curl -s https://api.github.com/repos/getsops/sops/releases/latest | grep tag_name | cut -d '"' -f4)
curl -LO "https://github.com/getsops/sops/releases/download/${SOPS_VERSION}/sops-${SOPS_VERSION}.linux.amd64"
sudo mv sops-${SOPS_VERSION}.linux.amd64 /usr/local/bin/sops
sudo chmod +x /usr/local/bin/sops

# Outils dev Ansible
pip3 install ansible ansible-builder ansible-lint yamllint pre-commit
```

### 2. Cloner le projet

```bash
git clone <URL_REPO> autoflow
cd autoflow
```

### 3. Configurer le `.env` de dev

```bash
# Copier le template
cp .env.example .env

# Éditer avec des valeurs de dev
# Utiliser un domaine fictif résolvable localement
DOMAIN=autoflow.local
AWX_ADMIN_PASSWORD=devpassword123
# ... etc.
```

!!! tip "Domaine local"
    Pour utiliser `autoflow.local`, ajouter dans `/etc/hosts` :
    ```
    127.0.0.1 autoflow.local awx.autoflow.local git.autoflow.local pki.autoflow.local monitoring.autoflow.local api.autoflow.local wizard.autoflow.local
    ```

### 4. Démarrer en dev

```bash
# Déploiement complet
docker compose up -d

# Ou uniquement les services essentiels
docker compose up -d traefik awx_postgres awx_web awx_task gitea event_engine
```

---

## Développement des services Python

### Deploy Wizard

```bash
cd services/deploy-wizard

# Créer un venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Lancer en mode dev (hot-reload)
uvicorn main:app --reload --port 9000

# Tests
pytest tests/ -v
```

### Event Engine

```bash
cd services/event-engine

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Variables d'environnement pour le dev
export AWX_URL=https://awx.autoflow.local
export AWX_TOKEN=devtoken
export EVENT_ENGINE_ADMIN_TOKEN=admintoken

# Lancer
uvicorn main:app --reload --port 8000
```

---

## Développement des playbooks

```bash
# Installer les collections nécessaires
ansible-galaxy collection install -r playbooks/collections/requirements.yml

# Linter les playbooks
ansible-lint playbooks/

# Tester en dry-run
ansible-playbook -i inventory/dev playbooks/deploy.yml --check --diff

# Tester sur un host local
ansible-playbook -i "localhost," -c local playbooks/test.yml
```

---

## Logs en développement

```bash
# Tous les logs en temps réel
docker compose logs -f

# Un service spécifique
docker compose logs -f awx_web

# Filtrer les erreurs
docker compose logs -f 2>&1 | grep -i "error\|exception"
```

---

## Pre-commit hooks

```bash
# Installer pre-commit
pip install pre-commit

# Créer .pre-commit-config.yaml
cat > .pre-commit-config.yaml << 'EOF'
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.5.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-json

  - repo: https://github.com/ansible/ansible-lint
    rev: v24.2.0
    hooks:
      - id: ansible-lint

  - repo: https://github.com/adrienverge/yamllint
    rev: v1.35.1
    hooks:
      - id: yamllint
        args: ["-d", "relaxed"]
EOF

# Activer
pre-commit install

# Tester manuellement
pre-commit run --all-files
```

---

## Variables d'environnement de développement recommandées

```bash
# .env pour dev local
DOMAIN=autoflow.local
AWX_ADMIN_PASSWORD=dev_pass_123
GITEA_ADMIN_PASSWORD=dev_gitea_123
AWX_SECRET_KEY=dev_secret_key_not_for_prod_123456789
POSTGRES_PASSWORD=dev_pg_pass
PKI_JWT_SECRET=dev_pki_jwt_secret
WIZARD_TOKEN=dev_wizard_token
EVENT_ENGINE_TOKEN=dev_event_token
EVENT_ENGINE_ADMIN_TOKEN=dev_admin_token
MONITORING_ADMIN_USER=admin
MONITORING_ADMIN_PASSWORD=dev_monitoring_pass
GITEA_REGISTRY_TOKEN=dev_registry_token
GITEA_WEBHOOK_SECRET=dev_webhook_secret
BACKUP_REPOSITORY=/tmp/dev-backup-repo
BACKUP_PASSWORD=dev_backup_pass

# TLS : utiliser des auto-signés en dev
# (le wizard génère automatiquement un CA si DOMAIN est configuré)
```
