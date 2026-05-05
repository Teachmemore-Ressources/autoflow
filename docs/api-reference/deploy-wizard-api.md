---
title: Deploy Wizard API
---

# Deploy Wizard API

Le Deploy Wizard expose une API REST sur `https://wizard.<DOMAIN>`. Elle est utilisée exclusivement par l'interface web du wizard — cette documentation est destinée aux intégrations avancées ou au débogage.

---

## Base URL

```
https://wizard.<DOMAIN>/api/
```

**Authentification** : HTTP Basic Auth (`wizard:<WIZARD_TOKEN>`)

---

## Configuration

### Lire la configuration courante

```http
GET /api/config
```

Retourne toutes les variables de configuration actuellement stockées dans `.env`.

```bash
curl -u wizard:<WIZARD_TOKEN> https://wizard.<DOMAIN>/api/config
```

Réponse :
```json
{
  "DOMAIN": "example.com",
  "AWX_ADMIN_PASSWORD": "...",
  "GITEA_ADMIN_PASSWORD": "...",
  "...": "..."
}
```

### Mettre à jour la configuration

```http
POST /api/config
```

**Corps** :
```json
{
  "DOMAIN": "new-domain.com",
  "AWX_ADMIN_PASSWORD": "nouveau_mot_de_passe"
}
```

```bash
curl -X POST \
  -u wizard:<WIZARD_TOKEN> \
  "https://wizard.<DOMAIN>/api/config" \
  -H "Content-Type: application/json" \
  -d '{"DOMAIN": "new-domain.com"}'
```

### Obtenir le schéma des champs

```http
GET /api/schema
```

Retourne la définition de tous les champs de configuration avec leurs métadonnées (label, type, section, description, valeur par défaut...).

```bash
curl -u wizard:<WIZARD_TOKEN> https://wizard.<DOMAIN>/api/schema
```

---

## Génération

### Générer des secrets / env

```http
GET /api/generate/{type}
```

| `type` | Description |
|---|---|
| `secrets` | Génère tous les mots de passe et tokens aléatoires |
| `env` | Génère le fichier `.env` complet |
| `compose` | Génère `docker-compose.yml` |

```bash
# Générer les secrets
curl -u wizard:<WIZARD_TOKEN> https://wizard.<DOMAIN>/api/generate/secrets

# Générer le .env complet
curl -u wizard:<WIZARD_TOKEN> https://wizard.<DOMAIN>/api/generate/env
```

### Chiffrer avec SOPS

```http
POST /api/encrypt
```

Chiffre le `.env` avec SOPS+Age.

```bash
curl -X POST -u wizard:<WIZARD_TOKEN> https://wizard.<DOMAIN>/api/encrypt
```

---

## Déploiement

### Lancer le déploiement (SSE stream)

```http
GET /api/deploy
```

Retourne un flux SSE avec les logs du déploiement en temps réel (`docker compose up -d`).

```bash
# Avec curl (streaming)
curl -N -u wizard:<WIZARD_TOKEN> https://wizard.<DOMAIN>/api/deploy

# Consommer avec EventSource (JavaScript)
# const es = new EventSource('/api/deploy');
# es.onmessage = e => console.log(e.data);
```

**Format SSE** :
```
data: [Traefik] Pulling image...
data: [AWX] Starting containers...
data: DEPLOY_COMPLETE
```

### Redémarrer les services (SSE stream)

```http
GET /api/restart
```

Identique à `/api/deploy` mais exécute `docker compose restart`.

### État global des services

```http
GET /api/status
```

Retourne le statut de tous les containers Docker.

```bash
curl -u wizard:<WIZARD_TOKEN> https://wizard.<DOMAIN>/api/status
```

Réponse :
```json
{
  "services": [
    {"name": "autoflow_traefik", "status": "running", "health": "healthy"},
    {"name": "autoflow_awx_web", "status": "running", "health": "healthy"},
    {"name": "autoflow_gitea", "status": "running", "health": null}
  ]
}
```

---

## Preflight & vérifications

### Preflight système complet

```http
GET /api/system/preflight
```

Lance tous les checks de pré-déploiement (Docker, ports, DNS, ressources...).

```bash
curl -u wizard:<WIZARD_TOKEN> https://wizard.<DOMAIN>/api/system/preflight
```

### Statut DNS EE (rapide)

```http
GET /api/preflight/ee-dns
```

Vérifie la configuration DNS de l'Execution Environment (depuis `.env`, sans Docker).

### Test DNS EE en streaming

```http
GET /api/preflight/ee-dns/stream
```

Lance un test DNS réel depuis le réseau bridge Docker. Retourne un flux SSE avec les logs.

### Appliquer la config DNS EE

```http
POST /api/preflight/apply-dns
```

Corps :
```json
{"dns": "8.8.8.8"}
```

### Statut des permissions

```http
GET /api/permissions/status
```

Vérifie si l'utilisateur courant a les permissions nécessaires (groupes Docker, sudo...).

### Corriger les permissions

```http
GET /api/permissions/fix
```

---

## Certificats

### Statut du certificat CA

```http
GET /api/cert-status
```

### Générer un certificat CA

```http
GET /api/generate-cert
```

### Télécharger le CA cert

```http
GET /api/download-ca
```

---

## Initialisation des services

### Initialiser Gitea (SSE stream)

```http
GET /api/init-gitea
```

Crée l'organisation, configure les webhooks, push les playbooks de base.

### Statut initialisation Gitea

```http
GET /api/init-gitea-status
```

### Créer le token AWX pour l'Event Engine

```http
GET /api/init-awx-token
```

---

## Execution Environments

### Statut des EE

```http
GET /api/ee/status
```

### Installer les dépendances (ansible-builder)

```http
GET /api/ee/install-deps
```

### Construire un EE (SSE stream)

```http
GET /api/ee/build
```

### Versions d'un EE

```http
GET /api/ee/versions/{ee_name}
```

### Rollback d'un EE

```http
GET /api/ee/rollback
```

---

## Sauvegarde

### Statut des sauvegardes

```http
GET /api/backup/status
```

### Lancer une sauvegarde manuelle (SSE stream)

```http
GET /api/backup/run
```

### Tester la restauration

```http
GET /api/backup/restore-test
```

### Installer le cron de sauvegarde

```http
POST /api/backup/cron/install
```

Corps :
```json
{"cron": "0 * * * *"}
```

### Désinstaller le cron

```http
DELETE /api/backup/cron/uninstall
```

---

## Conformité

### Statut du scanner

```http
GET /api/compliance/status
```

### Générer un rapport (SSE stream)

```http
GET /api/compliance/generate?framework=nist-800-53
```

Paramètres `framework` : `nist-800-53`, `cis-v8`, `iso-27001`, `pci-dss`, `soc2`.

### Télécharger le dernier rapport

```http
GET /api/compliance/report/latest
```

Retourne le PDF du dernier rapport généré.

```bash
curl -u wizard:<WIZARD_TOKEN> \
  "https://wizard.<DOMAIN>/api/compliance/report/latest" \
  -o compliance-$(date +%Y%m%d).pdf
```

### Rapport en Markdown

```http
GET /api/compliance/report/latest/markdown
```

---

## Grafana

### Statut export dashboards

```http
GET /api/grafana/export-status
```

### Exporter les dashboards (SSE stream)

```http
GET /api/grafana/export
```

Exporte tous les dashboards Grafana vers `grafana/dashboards/`.
