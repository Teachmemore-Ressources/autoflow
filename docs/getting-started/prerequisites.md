---
title: Prérequis
---

# Prérequis — Déploiement from scratch

Cette page couvre la préparation complète d'un serveur **Ubuntu 22.04 / 24.04** avant de lancer le Deploy Wizard.

!!! info "Temps estimé"
    15 à 20 minutes sur un serveur fraîchement installé avec accès internet.

---

## 1. Docker Engine + Docker Compose v2

Docker est le seul runtime requis. Docker Compose v2 est inclus automatiquement.

```bash
# Installation automatique via le script officiel Docker
curl -fsSL https://get.docker.com | sh
```

### Ajouter l'utilisateur au groupe docker

Obligatoire pour exécuter `docker` sans `sudo` :

```bash
sudo usermod -aG docker $USER
sudo newgrp docker
```

!!! warning "Déconnexion nécessaire"
    `newgrp docker` ouvre un nouveau shell avec le groupe actif. Si vous vous reconnectez en SSH, la commande `newgrp` n'est pas nécessaire — le groupe sera actif dès la connexion.

### Vérifier l'installation

```bash
docker --version
# Docker version 24.x.x, build xxxxxxx

docker compose version
# Docker Compose version v2.x.x
```

---

## 2. Dépendances système

```bash
sudo apt-get update
sudo apt-get install -y \
  python3 \
  python3-venv \
  python3-pip \
  make \
  git \
  age
```

| Package | Usage |
|---|---|
| `python3` + `python3-venv` | Environnement virtuel du Deploy Wizard |
| `python3-pip` | Gestionnaire de paquets Python |
| `make` | Commandes Autoflow (`make start`, `make wizard`, etc.) |
| `git` | Clonage du projet, gestion des playbooks |
| `age` | Chiffrement asymétrique des secrets (via SOPS) |

---

## 3. SOPS — Gestion des secrets chiffrés

SOPS (Secrets OPerationS) permet de chiffrer le fichier `.env` avec une clé Age. Le fichier chiffré `.env.enc` peut être versionné en git sans risque.

```bash
# Télécharger et installer SOPS v3.9.4
sudo curl -fsSL \
  "https://github.com/getsops/sops/releases/download/v3.9.4/sops-v3.9.4.linux.amd64" \
  -o /usr/local/bin/sops

sudo chmod +x /usr/local/bin/sops

# Vérifier
sops --version
# sops 3.9.4 (latest)
```

---

## 4. Générer la clé Age du serveur

Chaque serveur a sa propre clé Age. Cette clé **chiffre et déchiffre** le fichier `.env.enc`.

```bash
# Créer le répertoire de configuration Age
mkdir -p ~/.config/sops/age

# Générer la clé (clé privée + clé publique)
age-keygen -o ~/.config/sops/age/keys.txt
```

Exemple de sortie :
```
Public key: age1wn3csx59ga8kppeznnqrq08g42n7h44d6sx4r6tq2p3z9gqvfglq0v46s7
```

!!! danger "Sauvegardez cette clé maintenant"
    La clé Age est **la seule façon de déchiffrer** le fichier `.env.enc`.

    ```bash
    # Afficher la clé pour la copier
    cat ~/.config/sops/age/keys.txt
    ```

    Stockez-la dans :
    - Un gestionnaire de mots de passe (Bitwarden, 1Password, etc.)
    - Un coffre-fort sécurisé
    - Un support physique hors ligne

    **Sans cette clé, les secrets sont définitivement perdus.**

---

## 5. Cloner le projet

```bash
# Cloner depuis GitHub
git clone https://github.com/Teachmemore-Ressources/autoflow.git autoflow

cd autoflow
```

---

## 6. Préparer l'environnement virtuel Python

Le Deploy Wizard s'exécute dans un environnement virtuel Python isolé.

```bash
# Créer l'environnement virtuel
python3 -m venv .wizard-venv

# Activer (facultatif — make wizard le fait automatiquement)
source .wizard-venv/bin/activate
```

!!! note "Réinstallation"
    Si l'environnement est corrompu ou si vous montez de version Python :
    ```bash
    rm -rf .wizard-venv
    python3 -m venv .wizard-venv
    # make wizard installera les dépendances automatiquement
    ```

---

## 7. Désactiver le swap

!!! danger "Requis en production"
    Le swap doit être désactivé avant de lancer la stack. Voir [Pourquoi — Prérequis système](../overview/requirements.md#swap).

```bash
# Désactiver immédiatement
sudo swapoff -a

# Désactiver de façon permanente (supprimer les lignes swap de /etc/fstab)
sudo sed -i '/swap/d' /etc/fstab

# Vérifier
free -h | grep Swap
# Swap:          0B       0B       0B
```

---

## 8. Paramètres kernel — `make host-setup`

!!! warning "À faire avant `make start`"
    Autoflow requiert plusieurs paramètres kernel ajustés sur le host. Cette commande est **idempotente** — elle peut être relancée à tout moment sans risque.

```bash
cd autoflow
make host-setup
```

Paramètres appliqués et persistés dans `/etc/sysctl.d/10-autoflow.conf` :

| Paramètre | Valeur | Raison |
|---|---|---|
| `vm.overcommit_memory` | `1` | Redis AOF — `bgsave` / réécriture sans fork failure |
| `net.core.somaxconn` | `65535` | AWX callbacks + Prometheus scrape en burst |
| `net.ipv4.tcp_max_syn_backlog` | `65535` | Idem |
| `fs.inotify.max_user_watches` | `524288` | Gitea repos + Promtail log dirs |
| `fs.inotify.max_user_instances` | `512` | Idem |
| `vm.swappiness` | `10` | Empêche le swap des conteneurs |

!!! info "Détail technique"
    Sur un host avec peu de vCPUs (< 8), AWX peut être soumis à du CPU throttling. Si Docker envoie `SIGKILL` pendant une période de throttling, le signal est mis en queue mais jamais délivré — le conteneur devient impossible à arrêter. `make host-setup` ne corrige pas ce problème directement (c'est une contrainte matérielle), mais l'ensemble des `stop_grace_period` configurés dans `docker-compose.yml` évite que SIGKILL soit jamais envoyé en conditions normales. Voir [Prérequis système — Pourquoi 8 vCPUs](../overview/requirements.md#pourquoi-8-vcpus).

---

## 9. Vérification finale

Vérifiez que tout est en place avant de lancer le wizard :

```bash
# Docker
docker --version && docker compose version

# Python
python3 --version && python3 -m venv --help > /dev/null && echo "venv OK"

# Outils
make --version | head -1
git --version
age --version
sops --version

# Clé Age
ls -la ~/.config/sops/age/keys.txt

# Swap désactivé
free -h | grep Swap  # doit afficher 0B / 0B / 0B

# Paramètres kernel
sysctl vm.overcommit_memory net.core.somaxconn fs.inotify.max_user_watches vm.swappiness
# vm.overcommit_memory = 1
# net.core.somaxconn = 65535
# fs.inotify.max_user_watches = 524288
# vm.swappiness = 10
```

Sortie attendue (exemples) :
```
Docker version 24.0.7
Docker Compose version v2.23.3
Python 3.12.x
GNU Make 4.3
git version 2.43.0
age v1.1.1
sops 3.9.4
-rw------- 1 vagrant vagrant 256 May  1 00:00 /home/vagrant/.config/sops/age/keys.txt
```

---

## Récapitulatif des commandes

=== "Tout en une fois (Ubuntu 22.04/24.04)"

    ```bash
    # Docker
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker $USER
    sudo newgrp docker

    # Dépendances
    sudo apt-get update
    sudo apt-get install -y python3 python3-venv python3-pip make git age

    # SOPS
    sudo curl -fsSL \
      "https://github.com/getsops/sops/releases/download/v3.9.4/sops-v3.9.4.linux.amd64" \
      -o /usr/local/bin/sops && sudo chmod +x /usr/local/bin/sops

    # Clé Age
    mkdir -p ~/.config/sops/age
    age-keygen -o ~/.config/sops/age/keys.txt

    # Projet
    git clone https://github.com/Teachmemore-Ressources/autoflow.git autoflow
    cd autoflow

    # Environnement Python
    python3 -m venv .wizard-venv
    ```

---

## Étape suivante

Les prérequis sont installés. Passez au [Quick Start](quick-start.md) pour lancer le Deploy Wizard et déployer la stack.
