---
title: Prérequis système
---

# Prérequis système

## Système d'exploitation

| OS | Statut | Notes |
|---|---|---|
| **Ubuntu 22.04 LTS** | ✅ Testé et supporté | Recommandé |
| **Ubuntu 24.04 LTS** | ✅ Testé et supporté | Recommandé |
| Debian 11 / 12 | ✅ Compatible | Non testé systématiquement |
| RHEL 8/9 / Rocky / AlmaLinux | ⚠️ Compatible avec ajustements | SELinux à configurer |
| macOS | ❌ Non supporté | Docker Desktop limite les bind-mounts système |
| Windows | ❌ Non supporté | |

!!! warning "Architecture"
    Autoflow supporte uniquement **x86_64 (amd64)**. ARM64 (Apple Silicon, Raspberry Pi) n'est pas supporté — plusieurs images AWX n'ont pas de build ARM.

---

## Ressources matérielles

### Configuration minimale (lab / dev)

| Ressource | Minimum | Notes |
|---|---|---|
| CPU | 4 vCPUs | AWX est gourmand en CPU au démarrage |
| RAM | **8 Go** | 6 Go minimum absolu, instable en dessous |
| Disque système | 50 Go SSD | Images Docker, volumes |
| Réseau | 100 Mbps | Pour le pull des images et git sync |

### Configuration recommandée (production)

| Ressource | Recommandé | Notes |
|---|---|---|
| CPU | **8 vCPUs** | Pour absorber les pics de jobs parallèles |
| RAM | **16 Go** | Confortable pour tous les services |
| Disque système | **100 Go SSD NVMe** | + volume dédié pour les données |
| Disque données | **200 Go+** | PostgreSQL, Loki/MinIO (logs), Tempo (traces) |
| Réseau | 1 Gbps | Recommandé pour les transfers de registry |

!!! tip "Répartition RAM approximative"
    | Service | RAM typique |
    |---|---|
    | awx_web | ~500 Mo |
    | awx_task | ~800 Mo |
    | postgres | ~300 Mo |
    | redis | ~100 Mo |
    | gitea | ~200 Mo |
    | grafana | ~200 Mo |
    | loki | ~300 Mo |
    | prometheus | ~300 Mo |
    | autres services | ~500 Mo |
    | **Total** | **~3,2 Go** (idle) → **6+ Go** sous charge |

---

## Réseau

### DNS

Autoflow utilise des **sous-domaines** pour chaque service. Vous avez besoin d'un domaine ou d'un nom de domaine local (LAN) avec les enregistrements DNS suivants :

| Sous-domaine | Service |
|---|---|
| `awx.<DOMAIN>` | Interface AWX |
| `git.<DOMAIN>` | Gitea |
| `api.<DOMAIN>` | Autoflow API |
| `grafana.<DOMAIN>` | Grafana |
| `prometheus.<DOMAIN>` | Prometheus |
| `alertmanager.<DOMAIN>` | Alertmanager |
| `pki.<DOMAIN>` | PKI interne |

**Option 1 — Wildcard DNS** (recommandé en production) :
```
*.example.com  IN  A  <IP_SERVEUR>
```

**Option 2 — Enregistrements individuels** (pour un domaine LAN comme `.lan`) :
```
awx.mondomaine.lan     IN  A  192.168.1.10
git.mondomaine.lan     IN  A  192.168.1.10
api.mondomaine.lan     IN  A  192.168.1.10
grafana.mondomaine.lan IN  A  192.168.1.10
...
```

**Option 3 — `/etc/hosts` pour un lab** :
```
192.168.1.10  awx.mondomaine.lan git.mondomaine.lan api.mondomaine.lan grafana.mondomaine.lan prometheus.mondomaine.lan alertmanager.mondomaine.lan pki.mondomaine.lan
```

### Ports firewall

Ouvrir sur le serveur Autoflow :

| Port | Protocole | Direction | Usage |
|---|---|---|---|
| 80 | TCP | Entrant | HTTP → redirect HTTPS |
| 443 | TCP | Entrant | HTTPS (tous les services) |
| 2222 | TCP | Entrant | Git SSH (optionnel) |
| 22 | TCP | Entrant | SSH admin (pour tunnel wizard) |

!!! danger "Ne jamais exposer"
    - Port 9000 (Deploy Wizard) — accès local uniquement via tunnel SSH
    - Port 5432 (PostgreSQL) — jamais exposé
    - Port 6379 (Redis) — jamais exposé
    - Ports internes Docker (3001, 8000, 8001, etc.)

---

## Logiciels requis

### Sur le serveur cible

| Logiciel | Version minimale | Installation |
|---|---|---|
| **Docker Engine** | 24.0+ | `curl -fsSL https://get.docker.com \| sh` |
| **Docker Compose v2** | 2.20+ | Inclus avec Docker Engine (plugin) |
| **Python 3** | 3.10+ | `apt install python3 python3-venv` |
| **pip3** | 22+ | `apt install python3-pip` |
| **make** | 4.0+ | `apt install make` |
| **git** | 2.34+ | `apt install git` |
| **age** | 1.1+ | `apt install age` |
| **sops** | 3.9.4 | Voir section installation ci-dessous |

### Commandes d'installation complètes

```bash
# 1. Docker Engine
curl -fsSL https://get.docker.com | sh

# Ajouter l'utilisateur courant au groupe docker
sudo usermod -aG docker $USER
sudo newgrp docker

# Vérifier l'installation
docker --version          # Docker version 24.x.x
docker compose version    # Docker Compose version v2.x.x

# 2. Dépendances système
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip make git age

# 3. SOPS v3.9.4
sudo curl -fsSL \
  https://github.com/getsops/sops/releases/download/v3.9.4/sops-v3.9.4.linux.amd64 \
  -o /usr/local/bin/sops

sudo chmod +x /usr/local/bin/sops

# Vérifier
sops --version   # sops 3.9.4 (latest)
```

---

## Accès réseau sortant (serveur → internet)

Pour le premier déploiement, le serveur a besoin d'accès à :

| Destination | Usage |
|---|---|
| `registry-1.docker.io` | Pull des images Docker |
| `ghcr.io` | Images GitHub Container Registry |
| `quay.io` | Images Quay (AWX EE) |
| `galaxy.ansible.com` | Collections Ansible (build EE) |
| `github.com` | Téléchargement SOPS |
| `get.docker.com` | Installation Docker |
| `apt repos Ubuntu` | Packages système |

!!! note "Environnement air-gapped"
    Si le serveur n'a pas d'accès internet, voir la section [Configuration Air-gapped](../configuration/air-gapped.md). Il faut pré-charger les images Docker et les collections Ansible dans les EE au préalable.

---

## Accès SSH

Pour utiliser le Deploy Wizard depuis votre machine locale, vous avez besoin d'un accès SSH au serveur :

```bash
# Tunnel SSH depuis votre poste local
ssh -L 9000:localhost:9000 user@<IP_SERVEUR>

# Puis ouvrir dans le navigateur :
# http://localhost:9000
```

!!! tip "Pourquoi un tunnel ?"
    Le wizard tourne sur `127.0.0.1:9000` (localhost du serveur uniquement). Il ne doit jamais être exposé directement sur internet car il donne accès à la configuration complète de la stack, incluant tous les secrets.
