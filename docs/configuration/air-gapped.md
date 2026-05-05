---
title: Déploiement Air-gapped
---

# Déploiement Air-gapped

Un déploiement air-gapped est un déploiement sur un serveur **sans accès internet**. Autoflow supporte ce scénario avec quelques préparations préalables.

---

## Ce qui nécessite internet par défaut

| Composant | Besoin réseau | Solution air-gapped |
|---|---|---|
| Images Docker | Pull depuis DockerHub, quay.io, ghcr.io | Pré-charger dans le registry Gitea |
| Collections Ansible | Download depuis galaxy.ansible.com | Pré-installer dans les images EE |
| ansible-galaxy runtime | Appels galaxy au lancement des jobs | Supprimer avec `GALAXY_TASK_ENV_JSON` |
| Vérifications versions | API GitHub et DockerHub | Désactiver `VERSION_CHECK_INTERVAL=0` |

---

## Étape 1 — Pré-charger les images Docker

Sur une machine avec internet, sauvegarder toutes les images :

```bash
# Sauvegarder les images en fichiers tar
docker pull traefik:v2.11
docker pull postgres:15.17-alpine
docker pull redis:7.4.8-alpine
docker pull gitea/gitea:1.23-rootless
docker pull prom/prometheus:v3.11.1
docker pull grafana/grafana:12.4.2
# ... etc.

# Exporter en archive
docker save traefik:v2.11 | gzip > traefik-v2.11.tar.gz

# Transfert sur le serveur air-gapped (USB, rsync via bastion...)
# Puis charger :
docker load < traefik-v2.11.tar.gz
```

---

## Étape 2 — EE avec collections pré-installées

Construire les EE **depuis une machine avec internet**, puis les pousser :

```bash
# Sur la machine avec internet
make ee-build-push EE=base VERSION=1.0.0

# Exporter l'image
docker save git.<DOMAIN>/admin-gitea/ee-base:1.0.0 | gzip > ee-base-1.0.0.tar.gz

# Sur le serveur air-gapped
docker load < ee-base-1.0.0.tar.gz
docker push git.<DOMAIN>/admin-gitea/ee-base:1.0.0
```

---

## Étape 3 — Supprimer les appels galaxy.ansible.com

```bash
# Dans .env
GALAXY_TASK_ENV_JSON={"ANSIBLE_GALAXY_SERVER_LIST": ""}
```

Cela injecte `ANSIBLE_GALAXY_SERVER_LIST=""` dans tous les jobs AWX, supprimant tout appel à galaxy.ansible.com au runtime.

---

## Étape 4 — DNS EE

Laisser `EE_DNS_SERVER` **vide** — Docker utilise le DNS interne de l'organisation. Configurer le DNS interne pour résoudre `git.<DOMAIN>`.

---

## Étape 5 — Désactiver les vérifications de version

```bash
# Dans .env
VERSION_CHECK_INTERVAL=0    # Désactiver les vérifications de version
SCAN_INTERVAL=0             # Optionnel : désactiver les scans CVE si Trivy DB inaccessible
```

---

## Variables air-gapped récapitulatives

```bash
# .env — section air-gapped
GALAXY_TASK_ENV_JSON={"ANSIBLE_GALAXY_SERVER_LIST": ""}
EE_DNS_SERVER=
VERSION_CHECK_INTERVAL=0
```
