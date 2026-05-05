---
title: Authentification
---

# Authentification API

---

## AWX — Authentification

AWX supporte deux méthodes d'authentification.

### HTTP Basic Auth

```bash
curl -u admin:<AWX_ADMIN_PASSWORD> https://awx.<DOMAIN>/api/v2/ping/
```

Adapté aux scripts ponctuels. Non recommandé en production (mot de passe en clair dans les commandes).

### Bearer Token

Méthode recommandée pour les intégrations automatisées.

**Créer un token** :
```bash
curl -X POST \
  -u admin:<AWX_ADMIN_PASSWORD> \
  "https://awx.<DOMAIN>/api/v2/tokens/" \
  -H "Content-Type: application/json" \
  -d '{"description": "Mon intégration", "scope": "write"}'
```

Réponse :
```json
{
  "id": 5,
  "token": "abc123xyz...",
  "scope": "write",
  "expires": null,
  "description": "Mon intégration"
}
```

**Utiliser le token** :
```bash
curl -H "Authorization: Bearer abc123xyz..." \
  "https://awx.<DOMAIN>/api/v2/jobs/?status=running"
```

**Scopes disponibles** :

| Scope | Permissions |
|---|---|
| `read` | Lecture seule sur toutes les ressources |
| `write` | Lecture + écriture + lancement de jobs |

---

## Event Engine — Bearer Token

L'Event Engine utilise deux tokens distincts :

### Token webhook (public)

Utilisé pour envoyer des événements. Stocké dans `EVENT_ENGINE_TOKEN` (ou généré automatiquement).

```bash
curl -X POST "https://api.<DOMAIN>/event" \
  -H "Authorization: Bearer $EVENT_ENGINE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"source": "custom", "action": "ping"}'
```

### Token admin

Protège les endpoints `/admin/*`. Stocké dans `EVENT_ENGINE_ADMIN_TOKEN`.

```bash
curl "https://api.<DOMAIN>/admin/rules" \
  -H "Authorization: Bearer $EVENT_ENGINE_ADMIN_TOKEN"
```

---

## Webhooks Gitea — HMAC-SHA256

Les webhooks Gitea signent leurs payloads avec HMAC-SHA256. L'Event Engine valide cette signature.

```
X-Gitea-Signature: <hmac_sha256_hex>
```

Le secret partagé est `GITEA_WEBHOOK_SECRET` dans `.env`.

### Valider manuellement la signature

```python
import hmac, hashlib

def verify_gitea_signature(payload: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)
```

---

## Deploy Wizard — HTTP Basic Auth

Le Deploy Wizard est protégé par HTTP Basic Auth via Traefik.

```
Username: wizard
Password: <WIZARD_TOKEN>
```

```bash
curl -u wizard:<WIZARD_TOKEN> "https://wizard.<DOMAIN>/api/config"

# Ou avec l'en-tête Authorization
TOKEN=$(echo -n "wizard:<WIZARD_TOKEN>" | base64)
curl -H "Authorization: Basic $TOKEN" "https://wizard.<DOMAIN>/api/config"
```

---

## PKI API — JWT Bearer

L'API PKI utilise un JWT signé avec `PKI_JWT_SECRET`.

```bash
curl "https://pki.<DOMAIN>/api/v1/certificates" \
  -H "Authorization: Bearer $PKI_JWT_SECRET"
```

---

## Compliance API — Bearer Token

```bash
curl "https://api.<DOMAIN>/compliance/report/latest" \
  -H "Authorization: Bearer $COMPLIANCE_ADMIN_TOKEN" \
  -o report.pdf
```

---

## Monitoring — HTTP Basic Auth

Prometheus, Alertmanager et les métriques sont protégés par Basic Auth via Traefik.

```
Username: <MONITORING_ADMIN_USER>
Password: <MONITORING_ADMIN_PASSWORD>
```

```bash
curl -u "$MONITORING_ADMIN_USER:$MONITORING_ADMIN_PASSWORD" \
  "https://monitoring.<DOMAIN>/api/v1/query?query=up"
```

---

## Gitea API — Token d'accès personnel

```bash
curl "https://git.<DOMAIN>/api/v1/user" \
  -H "Authorization: token <GITEA_TOKEN>"
```

Pour les actions de registry :
```bash
docker login git.<DOMAIN> \
  -u <GITEA_USER> \
  -p <GITEA_REGISTRY_TOKEN>
```
