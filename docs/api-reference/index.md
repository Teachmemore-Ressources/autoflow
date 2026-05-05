---
title: API Reference
---

# API Reference

Autoflow expose plusieurs APIs REST. Cette section documente chaque endpoint avec ses paramètres, réponses et exemples.

---

## APIs disponibles

| API | Base URL | Auth | Description |
|---|---|---|---|
| [AWX API](autoflow-api.md) | `https://awx.<DOMAIN>/api/v2/` | Basic / Bearer | API AWX complète |
| [Event Engine API](event-engine-api.md) | `https://api.<DOMAIN>/` | Bearer | Webhooks & événements |
| [Deploy Wizard API](deploy-wizard-api.md) | `https://wizard.<DOMAIN>/api/` | Basic (WIZARD_TOKEN) | Configuration & déploiement |
| [Authentification](authentication.md) | — | — | Guide d'authentification |

---

## Conventions

### Format des réponses

Toutes les APIs retournent du JSON. Les réponses AWX suivent le format paginé AWX :

```json
{
  "count": 42,
  "next": "https://awx.<DOMAIN>/api/v2/jobs/?page=2",
  "previous": null,
  "results": [...]
}
```

### Codes HTTP

| Code | Signification |
|---|---|
| `200` | Succès |
| `201` | Ressource créée |
| `202` | Accepté (traitement asynchrone) |
| `204` | Succès sans contenu |
| `400` | Requête invalide |
| `401` | Non authentifié |
| `403` | Accès refusé |
| `404` | Ressource introuvable |
| `429` | Rate limit dépassé |
| `500` | Erreur serveur |

### Rate limiting

L'Event Engine applique un rate limit configurable via `RATE_LIMIT` (défaut : `100/minute` par IP).

En cas de dépassement :
```http
HTTP/1.1 429 Too Many Requests
Retry-After: 60
```

---

## Exemples rapides

### AWX — lancer un job

```bash
curl -X POST \
  -u admin:<AWX_ADMIN_PASSWORD> \
  "https://awx.<DOMAIN>/api/v2/job_templates/<ID>/launch/" \
  -H "Content-Type: application/json" \
  -d '{"extra_vars": {"target_host": "192.168.1.1"}}'
```

### Event Engine — envoyer un événement

```bash
curl -X POST \
  "https://api.<DOMAIN>/event" \
  -H "Authorization: Bearer $EVENT_ENGINE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"source": "custom", "action": "deploy", "ref": "main"}'
```

### Deploy Wizard — lire la configuration

```bash
curl "https://wizard.<DOMAIN>/api/config" \
  -u wizard:$WIZARD_TOKEN
```
