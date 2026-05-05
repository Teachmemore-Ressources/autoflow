---
title: Mises à jour
---

# Mises à jour

---

## Mettre à jour AWX

```bash
# 1. Éditer la version dans .env
AWX_VERSION=24.7.0

# 2. Rebuilder l'image patchée
make awx-build AWX_VERSION=24.7.0

# 3. Redémarrer AWX (migrations auto)
docker compose up -d --force-recreate awx_migrate awx_web awx_task receptor
```

!!! info "`down -v` — volumes critiques protégés"
    Les volumes critiques (PostgreSQL, Redis, Gitea, PKI) sont `external: true` — `docker compose down -v` ne peut pas les supprimer. Les volumes non-critiques (Prometheus, Loki, Grafana...) seront recréés vides au prochain `up`. Utiliser `docker compose down` ou `up -d --force-recreate` reste la meilleure pratique.

---

## Mettre à jour une image Docker

```bash
# Voir les versions pinned
make images-update

# Mettre à jour une image dans docker-compose.yml
# Changer la version, puis :
docker compose pull <service>
docker compose up -d --force-recreate <service>
```

---

## Mettre à jour Gitea

```bash
# Changer la version dans docker-compose.yml
# image: gitea/gitea:1.24-rootless

docker compose pull gitea
docker compose up -d --force-recreate gitea
```

---

## Avant toute mise à jour — checklist

- [ ] Backup effectué et vérifié (`make backup`)
- [ ] `.env.enc` commité dans git
- [ ] Fenêtre de maintenance communiquée
- [ ] Plan de rollback défini
