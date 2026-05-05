---
title: Construire un EE
---

# Construire un Execution Environment

Les Execution Environments (EE) sont des images Docker utilisées par AWX pour exécuter les playbooks. Ils encapsulent Ansible, les collections, et les dépendances Python/système.

---

## Prérequis

```bash
# ansible-builder >= 3.0
pip install ansible-builder

# Docker (ou Podman)
docker --version

# Accès au registry Gitea
docker login git.<DOMAIN> -u <user> -p <GITEA_REGISTRY_TOKEN>
```

---

## Structure d'un EE

```
awx/execution-environments/
├── ee-base/
│   ├── execution-environment.yml   # Définition principale
│   ├── requirements.yml            # Collections Ansible
│   ├── requirements.txt            # Dépendances Python
│   └── bindep.txt                  # Paquets système
└── ee-network/
    ├── execution-environment.yml
    ├── requirements.yml
    └── requirements.txt
```

---

## Fichier `execution-environment.yml`

```yaml
# execution-environment.yml
---
version: 3

build_arg_defaults:
  ANSIBLE_GALAXY_SERVER_LIST: automation_hub

images:
  base_image:
    name: quay.io/ansible/awx-ee:latest

dependencies:
  galaxy: requirements.yml
  python: requirements.txt
  system: bindep.txt

additional_build_steps:
  prepend_galaxy:
    - RUN pip3 install --upgrade pip

  append_final:
    - RUN ansible --version
    - COPY files/ansible.cfg /etc/ansible/ansible.cfg
```

---

## Fichier `requirements.yml`

```yaml
# requirements.yml
---
collections:
  - name: ansible.netcommon
    version: ">=5.0.0"
  - name: cisco.ios
    version: ">=5.0.0"
  - name: arista.eos
  - name: community.general
    version: ">=8.0.0"
  - name: community.docker

roles: []
```

---

## Fichier `requirements.txt`

```txt
# requirements.txt
netmiko>=4.3.0
napalm>=4.1.0
paramiko>=3.4.0
jinja2>=3.1.0
pyyaml>=6.0
```

---

## Fichier `bindep.txt`

```txt
# bindep.txt — paquets système (format bindep)
openssh-client [platform:dpkg]
sshpass [platform:dpkg]
git [platform:dpkg]
rsync [platform:dpkg]
```

---

## Construire l'image

```bash
cd awx/execution-environments/ee-network

# Build simple (tag local)
ansible-builder build \
  --tag git.<DOMAIN>/<ORG>/ee-network:latest \
  --context /tmp/ee-build-context \
  --verbosity 2

# Build avec un tag versionnée
VERSION=$(date +%Y%m%d)
ansible-builder build \
  --tag git.<DOMAIN>/<ORG>/ee-network:${VERSION} \
  --tag git.<DOMAIN>/<ORG>/ee-network:latest
```

---

## Pousser vers le registry Gitea

```bash
# Pousser l'image
docker push git.<DOMAIN>/<ORG>/ee-network:latest
docker push git.<DOMAIN>/<ORG>/ee-network:${VERSION}

# Vérifier dans Gitea
# UI : Packages → Container → ee-network
```

---

## Enregistrer l'EE dans AWX

### Via l'UI AWX

1. **Administration → Execution Environments → Add**
2. Remplir :
   - **Name** : `EE Network`
   - **Image** : `git.<DOMAIN>/<ORG>/ee-network:latest`
   - **Pull** : `Always` (en dev : `Missing`)
   - **Credential** : sélectionner le credential Container Registry Gitea
3. **Save**

### Via l'API AWX

```bash
curl -X POST \
  -u admin:<PASS> \
  "https://awx.<DOMAIN>/api/v2/execution_environments/" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "EE Network",
    "image": "git.<DOMAIN>/<ORG>/ee-network:latest",
    "pull": "always",
    "credential": <CREDENTIAL_ID>,
    "organization": 1
  }'
```

---

## Pipeline CI/CD pour les EE (Gitea Actions)

```yaml
# .gitea/workflows/build-ee.yml
name: Build EE

on:
  push:
    paths:
      - 'execution-environments/**'
    branches: [main]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install ansible-builder
        run: pip install ansible-builder

      - name: Login to registry
        run: docker login git.${{ vars.DOMAIN }} -u ${{ secrets.REGISTRY_USER }} -p ${{ secrets.REGISTRY_TOKEN }}

      - name: Build EE
        run: |
          cd execution-environments/ee-network
          VERSION=$(date +%Y%m%d)
          ansible-builder build \
            --tag git.${{ vars.DOMAIN }}/${{ vars.ORG }}/ee-network:${VERSION} \
            --tag git.${{ vars.DOMAIN }}/${{ vars.ORG }}/ee-network:latest

      - name: Push EE
        run: |
          docker push git.${{ vars.DOMAIN }}/${{ vars.ORG }}/ee-network:latest
          docker push git.${{ vars.DOMAIN }}/${{ vars.ORG }}/ee-network:$(date +%Y%m%d)
```

---

## Déboguer un EE

```bash
# Lancer un shell dans l'EE pour tester
docker run --rm -it git.<DOMAIN>/<ORG>/ee-network:latest bash

# Depuis le shell de l'EE :
ansible --version
python3 -c "import netmiko; print(netmiko.__version__)"
ansible-galaxy collection list

# Tester un playbook simple
ansible-playbook -i "localhost," -c local \
  -e "ansible_python_interpreter=/usr/bin/python3" \
  test-connectivity.yml
```

---

## Rollback d'un EE

```bash
# Lister les tags disponibles
curl -sk "https://git.<DOMAIN>/api/v1/packages/<ORG>/container/ee-network/tags/list" \
  -H "Authorization: token <GITEA_TOKEN>"

# Re-tagger une version précédente comme latest
docker pull git.<DOMAIN>/<ORG>/ee-network:20240101
docker tag git.<DOMAIN>/<ORG>/ee-network:20240101 git.<DOMAIN>/<ORG>/ee-network:latest
docker push git.<DOMAIN>/<ORG>/ee-network:latest

# Forcer AWX à re-pull
# AWX UI : Execution Environments → EE Network → Edit → Pull: Always → Save
```
