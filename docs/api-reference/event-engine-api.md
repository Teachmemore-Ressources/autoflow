---
title: Event Engine API
---

# Event Engine API

L'Event Engine expose une API REST sur `https://api.<DOMAIN>`.

---

## Base URL

```
https://api.<DOMAIN>/
```

---

## Authentification

Voir [Authentification](authentication.md). Les endpoints `/admin/*` nécessitent `EVENT_ENGINE_ADMIN_TOKEN`.

---

## Endpoints opérationnels

### Health check

```http
GET /health
```

Accès public. Retourne l'état du service.

```bash
curl https://api.<DOMAIN>/health
```

Réponse :
```json
{
  "status": "ok",
  "version": "1.0.0",
  "awx_connected": true,
  "uptime_seconds": 3600
}
```

---

### Envoyer un événement personnalisé

```http
POST /event
```

Permet de déclencher un job AWX depuis n'importe quelle source externe.

**Corps de la requête** :
```json
{
  "source": "custom",
  "action": "deploy",
  "ref": "main",
  "extra_vars": {
    "environment": "production",
    "version": "1.5.0"
  }
}
```

| Champ | Type | Requis | Description |
|---|---|---|---|
| `source` | string | oui | Identifiant de la source (`custom`, `github`, `gitlab`, ...) |
| `action` | string | oui | Action déclenchante (`push`, `deploy`, `release`, ...) |
| `ref` | string | non | Branche ou tag Git |
| `extra_vars` | object | non | Variables additionnelles passées au job AWX |

```bash
curl -X POST "https://api.<DOMAIN>/event" \
  -H "Authorization: Bearer $EVENT_ENGINE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "source": "custom",
    "action": "deploy",
    "ref": "v2.1.0",
    "extra_vars": {"env": "prod"}
  }'
```

Réponse :
```json
{
  "status": "accepted",
  "job_id": 47,
  "template_id": 12
}
```

---

## Webhooks

### Webhook Gitea

```http
POST /webhook/gitea
```

Endpoint appelé automatiquement par Gitea sur les événements configurés (push, PR, release...).

**Headers requis** :
```
X-Gitea-Event: push
X-Gitea-Signature: sha256=<hmac>
Content-Type: application/json
```

```bash
# Test manuel (sans signature)
curl -X POST "https://api.<DOMAIN>/webhook/gitea" \
  -H "X-Gitea-Event: push" \
  -H "Content-Type: application/json" \
  -d '{"repository": {"name": "network-playbooks"}, "ref": "refs/heads/main", "pusher": {"login": "admin"}}'
```

**Événements supportés** :

| Événement | Header `X-Gitea-Event` | Déclencheur |
|---|---|---|
| Push | `push` | Commit poussé |
| Pull Request | `pull_request` | PR ouverte/mise à jour |
| Release | `release` | Nouvelle release créée |
| Tag | `create` | Tag créé |

---

### Webhook GitHub

```http
POST /webhook/github
```

Identique au webhook Gitea mais avec la signature GitHub (`X-Hub-Signature-256`).

```bash
curl -X POST "https://api.<DOMAIN>/webhook/github" \
  -H "X-GitHub-Event: push" \
  -H "X-Hub-Signature-256: sha256=<hmac>" \
  -H "Content-Type: application/json" \
  -d '{"repository": {"name": "playbooks"}, "ref": "refs/heads/main"}'
```

---

### Webhook Alertmanager

```http
POST /webhook/alertmanager
```

Reçoit les alertes Prometheus/Alertmanager pour déclencher des jobs de remédiation.

```json
{
  "alerts": [
    {
      "status": "firing",
      "labels": {
        "alertname": "DiskFull",
        "instance": "server01",
        "severity": "critical"
      },
      "annotations": {
        "summary": "Disk full on server01"
      }
    }
  ]
}
```

---

## Endpoints admin

Tous ces endpoints nécessitent `Authorization: Bearer $EVENT_ENGINE_ADMIN_TOKEN`.

### Lister les règles de routage

```http
GET /admin/rules
```

```bash
curl "https://api.<DOMAIN>/admin/rules" \
  -H "Authorization: Bearer $EVENT_ENGINE_ADMIN_TOKEN"
```

Réponse :
```json
[
  {
    "id": "gitea-push-main",
    "source": "gitea",
    "action": "push",
    "ref_pattern": "refs/heads/main",
    "template_id": 7,
    "enabled": true
  }
]
```

### Recharger les règles

```http
POST /admin/rules/reload
```

```bash
curl -X POST "https://api.<DOMAIN>/admin/rules/reload" \
  -H "Authorization: Bearer $EVENT_ENGINE_ADMIN_TOKEN"
```

### Statistiques de dédoublonnage

```http
GET /admin/dedup/stats
```

```bash
curl "https://api.<DOMAIN>/admin/dedup/stats" \
  -H "Authorization: Bearer $EVENT_ENGINE_ADMIN_TOKEN"
```

### Vider le cache de dédoublonnage

```http
POST /admin/dedup/clear
```

### Statistiques de la file d'attente

```http
GET /admin/queue/stats
```

Réponse :
```json
{
  "queue_size": 0,
  "processed": 1247,
  "failed": 3,
  "dlq_size": 1
}
```

### Dead Letter Queue (DLQ)

```http
GET /admin/dlq
```

Liste les événements qui ont échoué après tous les retries.

```http
POST /admin/dlq/{event_id}/retry
```

Re-tenter un événement depuis la DLQ.

```http
DELETE /admin/dlq
```

Vider la DLQ.

### Détail d'un événement

```http
GET /admin/events/{event_id}
```

```bash
curl "https://api.<DOMAIN>/admin/events/abc-123" \
  -H "Authorization: Bearer $EVENT_ENGINE_ADMIN_TOKEN"
```

### Schedules (jobs planifiés)

```http
GET /admin/schedules
POST /admin/schedules/reload
```

---

## Format de log des événements

Chaque événement traité est loggé en JSON :

```json
{"time": "2024-05-01T02:00:00Z", "event": "webhook_received", "source": "gitea", "action": "push", "ref": "refs/heads/main"}
{"time": "2024-05-01T02:00:01Z", "event": "rule_matched", "rule_id": "gitea-push-main", "template_id": 7}
{"time": "2024-05-01T02:00:02Z", "event": "job_launched", "template_id": 7, "job_id": 48}
```
