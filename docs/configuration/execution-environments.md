---
title: Execution Environments
---

# Execution Environments (EE)

Les Execution Environments sont les **images Docker** qu'AWX utilise pour exécuter les playbooks Ansible. Elles contiennent `ansible-core`, les collections Ansible, les dépendances Python et les outils système nécessaires.

---

## EE incluses dans Autoflow

| EE | Description | Collections principales |
|---|---|---|
| `ee-base` | Généraliste — tous les playbooks courants | `community.general`, `ansible.posix`, `community.crypto`, `community.proxmox` |
| `ee-network` | Équipements réseau (Cisco, Arista, NAPALM) | `cisco.ios`, `arista.eos`, `ansible.netcommon`, `community.network` |
| `ee-security` | Audit et conformité | `community.crypto`, modules audit |

---

## Structure d'une définition EE

Fichier : `execution-environments/<nom>/execution-environment.yml`

```yaml
# execution-environments/base/execution-environment.yml
version: 3

images:
  base_image:
    name: 'quay.io/ansible/awx-ee:latest'  # Image de base AWX officielle

dependencies:
  galaxy:
    collections:
      - name: community.general
        version: ">=9.0.0"
      - name: ansible.posix
        version: ">=1.5.0"
      - name: community.crypto
        version: ">=2.18.0"
      - name: community.proxmox
        version: ">=1.6.0"

  python:
    - requests>=2.31.0
    - jinja2>=3.1.0
    - pyyaml>=6.0

  system: []

options:
  package_manager_path: /usr/bin/dnf
```

---

## Prérequis au build

```bash
# 1. Installer ansible-builder
make ee-deps

# 2. Faire confiance au CA Autoflow (pour pousser vers le registry Gitea)
make docker-trust-ca

# 3. S'assurer que BUILD_PYCMD pointe vers Python 3.12+
echo $BUILD_PYCMD   # /usr/bin/python3.12
```

---

## Commandes de build

```bash
# Builder une EE (génère l'image localement)
make ee-build EE=base VERSION=1.0.0

# Pousser vers le registry Gitea
make ee-push EE=base VERSION=1.0.0

# Build + push en une commande
make ee-build-push EE=base VERSION=1.0.0

# Lister les EE disponibles
make ee-list
```

### Runtime Docker vs Podman

```bash
# Auto-détection : podman > docker
# Forcer Docker explicitement :
make ee-build EE=base CONTAINER_RUNTIME=docker

# Vérifier le runtime actif
make ee-build EE=base 2>&1 | grep runtime
```

!!! tip "Toujours utiliser Docker sur Autoflow"
    Si Podman est installé sur le serveur, forcer `CONTAINER_RUNTIME=docker`. AWX utilise Docker — les images doivent être dans le même store Docker.

---

## Assigner un EE à un Job Template AWX

1. Dans AWX : **Templates → votre template → Edit**
2. Champ **Execution Environment** : sélectionner `git.<DOMAIN>/admin-gitea/ee-base:1.0.0`
3. Sauvegarder

Pour que l'image soit accessible à AWX, elle doit être dans le registry Gitea ET AWX doit avoir un **Container Registry credential** configuré.

### Créer le credential registry dans AWX

1. **Credentials → Add**
2. Type : `Container Registry`
3. Registry URL : `git.<DOMAIN>`
4. Username : `admin-gitea`
5. Password : `GITEA_REGISTRY_TOKEN` (ou `GITEA_ADMIN_PASSWORD`)

---

## Variables d'environnement

| Variable | Défaut | Description |
|---|---|---|
| `EE_DEFAULT_VERSION` | `latest` | Tag appliqué aux images buildées |
| `BUILD_PYCMD` | `/usr/bin/python3.12` | Python pour ansible-builder |
| `GITEA_USER` | `admin-gitea` | Owner des images dans le registry |
| `EE_DNS_SERVER` | *(vide)* | DNS pour les EE — laisser vide |
| `DOCKER_GID` | `999` | GID du socket Docker hôte |

---

## Air-gapped : EE sans accès internet

En environnement sans internet, les collections doivent être pré-installées dans l'image au build time.

```yaml
# execution-environment.yml air-gapped
version: 3

images:
  base_image:
    name: 'git.<DOMAIN>/admin-gitea/awx-ee-base:24.6.1'  # Image locale

dependencies:
  galaxy:
    collections:
      - name: community.general
        # ansible-builder télécharge les collections au BUILD time
        # pas au runtime AWX
```

Supprimer les appels galaxy au runtime dans `.env` :
```bash
GALAXY_TASK_ENV_JSON={"ANSIBLE_GALAXY_SERVER_LIST": ""}
```
