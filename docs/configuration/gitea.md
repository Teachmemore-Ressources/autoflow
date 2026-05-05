---
title: Gitea
---

# Gitea

Gitea est le hub GitOps d'Autoflow : dépôts Git, CI/CD via Gitea Actions, registry Docker OCI et webhooks vers l'Event Engine.

---

## Services Gitea dans Autoflow

| Container | Rôle |
|---|---|
| `autoflow_gitea` | Gitea principal (Git + Registry + Actions) |
| `autoflow_gitea_postgres` | Base PostgreSQL dédiée Gitea |
| `autoflow_act_runner` | Runner CI/CD Gitea Actions |

---

## Initialisation

Au premier démarrage, Gitea crée automatiquement le compte admin depuis les variables `.env` :

```bash
GITEA_ADMIN_USER=admin-gitea
GITEA_ADMIN_PASSWORD=<strong_password>
GITEA_ADMIN_EMAIL=admin@client.example.com
```

!!! warning "Compte admin immuable"
    Le username admin ne peut pas être changé après la création. Choisir définitivement avant le premier démarrage.

---

## Registry Docker OCI

Le registry est intégré à Gitea sur le même port (`:3001`). Les images Docker sont stockées sous `git.<DOMAIN>/<GITEA_USER>/<image>:<tag>`.

### Login au registry

```bash
# Faire confiance au CA d'abord
make docker-trust-ca

# Login avec les credentials Gitea
docker login git.<DOMAIN>
# Username: admin-gitea
# Password: <GITEA_ADMIN_PASSWORD>

# Ou avec le token registry dédié
docker login git.<DOMAIN> -u admin-gitea -p "$GITEA_REGISTRY_TOKEN"
```

### Push d'une image

```bash
docker tag myimage:latest git.<DOMAIN>/admin-gitea/myimage:1.0.0
docker push git.<DOMAIN>/admin-gitea/myimage:1.0.0
```

### Limites de stockage

```bash
GITEA_REGISTRY_OWNER_LIMIT=-1   # -1 = illimité (recommandé en dev)
GITEA_REGISTRY_IMAGE_LIMIT=-1   # Limiter en production (ex: 10737418240 = 10 Go)
```

---

## Gitea Actions (CI/CD)

### Runner

Le runner `act_runner` exécute les workflows dans des conteneurs Docker. Il est compatible avec la syntaxe GitHub Actions.

Labels du runner : `autoflow, linux, docker, ansible`

Pour forcer un workflow à utiliser ce runner :
```yaml
# .gitea/workflows/deploy.yml
runs-on: autoflow
```

### Enregistrement du runner

```bash
make gitea-init-runner
```

Ce script :
1. Attend que Gitea soit disponible via son URL publique
2. Appelle `GET /api/v1/admin/runners/registration-token` (Gitea 1.21+)
3. Sauvegarde `GITEA_RUNNER_TOKEN` dans `.env`
4. Recrée le conteneur `act_runner` avec `--force-recreate` (nécessaire pour relire les env vars)

!!! warning "docker compose restart ne suffit pas"
    `docker compose restart act_runner` ne relit pas les variables d'environnement — le token reste vide. Utiliser `make gitea-init-runner` qui fait `up -d --force-recreate`.

### Workflow exemple

```yaml
# .gitea/workflows/lint.yml
name: Lint Playbooks

on:
  push:
    branches: [main, develop]

jobs:
  lint:
    runs-on: autoflow
    container:
      image: git.<DOMAIN>/admin-gitea/ee-base:latest
    steps:
      - uses: actions/checkout@v4
      - name: Run ansible-lint
        run: ansible-lint playbooks/
```

---

## Webhooks → Event Engine

Pour déclencher des jobs AWX sur les événements Gitea :

1. **Repository → Settings → Webhooks → Add Webhook → Gitea**
2. Target URL : `https://api.<DOMAIN>/events/webhook`
3. Secret : valeur de `GITEA_WEBHOOK_SECRET`
4. Events : Push, Create, Release, selon vos besoins

Le secret est vérifié par l'Event Engine via HMAC-SHA256.

---

## Métriques Prometheus

Gitea expose des métriques Prometheus sur `/metrics`, protégées par bearer token :

```bash
GITEA_METRICS_TOKEN=<generated>
```

Prometheus scrappe ce endpoint avec le header `Authorization: Bearer <GITEA_METRICS_TOKEN>`.

---

## Variables clés

| Variable | Notes |
|---|---|
| `GITEA_SECRET_KEY` | Chiffrement interne Gitea — générer une seule fois |
| `GITEA_INTERNAL_TOKEN` | Token API interne — générer une seule fois |
| `GITEA_LOG_LEVEL` | `Warn` en prod, `Info` pour debug |
| `GITEA_USER` | Owner des images EE dans le registry |
