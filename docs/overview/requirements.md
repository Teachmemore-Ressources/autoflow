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

### Configuration minimale absolue (lab / dev uniquement)

!!! danger "Ne pas utiliser en production"
    La configuration minimale ci-dessous permet de démarrer la stack dans un environnement de test. En dessous de 8 vCPUs, AWX est soumis à du **CPU throttling** qui peut rendre les conteneurs impossibles à arrêter proprement. Voir [Pourquoi 8 vCPUs ?](#pourquoi-8-vcpus).

| Ressource | Minimum absolu | Notes |
|---|---|---|
| CPU | 4 vCPUs | Throttling fréquent — instable pour les opérations stop/restart |
| RAM | 8 Go | 6 Go minimum absolu, instable en dessous |
| Disque système | 50 Go SSD | Images Docker, volumes |
| Réseau | 100 Mbps | Pour le pull des images et git sync |

### Configuration recommandée (production)

| Ressource | Recommandé | Notes |
|---|---|---|
| CPU | **8 vCPUs** | Minimum pour absorber les bursts AWX sans throttling |
| RAM | **16 Go** | Confortable pour tous les services sous charge |
| Disque système | **150 Go SSD NVMe** | Images Docker + volumes |
| Disque données | **200 Go+** | PostgreSQL, Loki/MinIO (logs), Tempo (traces) |
| Réseau | 1 Gbps | Pour les transfers de registry |
| Swap | **Désactivé** | Voir [Swap](#swap) |

### Configuration optimale (production haute disponibilité)

| Ressource | Optimal |
|---|---|
| CPU | 12-16 vCPUs |
| RAM | 32 Go |
| Disque système | 200 Go NVMe |
| Disque données | 500 Go+ NVMe séparé |

### Répartition RAM approximative

!!! tip "Consommation réelle par service"
    | Service | RAM typique (idle) | RAM sous charge |
    |---|---|---|
    | awx_web | ~500 Mo | ~800 Mo |
    | awx_task | ~800 Mo | ~1.5 Go |
    | postgres (AWX) | ~300 Mo | ~500 Mo |
    | redis | ~100 Mo | ~220 Mo |
    | gitea + gitea_postgres | ~350 Mo | ~600 Mo |
    | grafana + prometheus | ~500 Mo | ~800 Mo |
    | loki + tempo | ~400 Mo | ~700 Mo |
    | traefik + autres | ~300 Mo | ~400 Mo |
    | **Total** | **~3.3 Go** | **~5.5-6 Go** |

    Avec 8 Go de RAM, la marge est très faible sous charge. **16 Go** est le vrai minimum production.

---

## Swap

!!! danger "Désactiver le swap sur le host Docker"
    Le swap doit être **désactivé** sur le serveur hébergeant Autoflow :

    - Un conteneur qui swap provoque des **latences importantes** (PostgreSQL, AWX)
    - Le swap masque la pression mémoire qui devrait déclencher l'OOM eviction
    - `vm.swappiness = 10` (appliqué par `make host-setup`) empêche l'utilisation du swap même s'il est présent

    ```bash
    # Désactiver immédiatement
    sudo swapoff -a

    # Désactiver de façon permanente
    sudo sed -i '/swap/d' /etc/fstab

    # Vérifier
    free -h | grep Swap
    # Swap:          0B       0B       0B
    ```

---

## Paramètres kernel (host Docker)

Autoflow requiert plusieurs paramètres kernel ajustés sur le **host Docker**. Ces paramètres persistent après reboot.

```bash
# Appliquer tous les paramètres en une commande
make host-setup
```

### Détail des paramètres

| Paramètre | Valeur requise | Valeur par défaut | Impact si non appliqué |
|---|---|---|---|
| `vm.overcommit_memory` | `1` | `0` | Redis : `bgsave` / `BGREWRITEAOF` échouent silencieusement → corruption AOF |
| `net.core.somaxconn` | `65535` | `4096` | AWX callbacks, Prometheus scrape : SYN packets droppés sous charge |
| `net.ipv4.tcp_max_syn_backlog` | `65535` | `512` | Idem — reject de connexions entrantes en burst |
| `fs.inotify.max_user_watches` | `524288` | `8192–61604` | Gitea / Promtail : erreur « inotify limit reached » |
| `fs.inotify.max_user_instances` | `512` | `128` | Idem |
| `vm.swappiness` | `10` | `60` | Swap de conteneurs → latence + masque pression mémoire |

!!! note "Fichier persistant"
    `make host-setup` écrit `/etc/sysctl.d/10-autoflow.conf` et applique les valeurs immédiatement sans redémarrage.

---

## Pourquoi 8 vCPUs ?

### Le bug CPU throttle + SIGKILL

AWX (`awx_web` + `awx_task`) est limité à `cpus: 2.0` dans `docker-compose.yml`. Avec 4 vCPUs disponibles sur le host, AWX peut consommer **jusqu'à 50% de tous les CPUs** lors d'un burst (job lancé, healthcheck, compaction DB simultanés).

Quand le cgroup atteint sa limite CPU, le kernel **suspend les threads** d'AWX pendant la période de throttling. Si à ce moment Docker envoie un `SIGKILL` (après l'expiration du `stop_grace_period`), le signal est mis en queue (`SigPnd = 0x100`) mais **n'est jamais délivré** — le process ne tourne pas pour le recevoir.

**Résultat observable** :
```bash
$ docker stop autoflow_awx_task
Error response from daemon: cannot stop container: tried to kill container,
but did not receive an exit event
```

Les conteneurs apparaissent **immortels** — `docker rm -f` échoue également.

**Sur 8 vCPUs**, AWX n'utilise que 25% des CPUs disponibles → throttling rare → le `SIGKILL` est délivré normalement dans la majorité des cas.

**Protection supplémentaire** : `stop_grace_period: 60s` sur `awx_web` et `awx_task` donne à supervisord le temps de s'arrêter proprement via SIGTERM avant que SIGKILL soit envoyé — le problème ne se pose alors plus du tout.

!!! info "Voir aussi"
    Pour la procédure de récupération si des conteneurs sont déjà bloqués : [Runbook — Conteneur AWX impossible à arrêter](../runbook/troubleshooting/awx.md#probleme-18-conteneur-awx-impossible-a-arreter-sigkill-deferred).

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
