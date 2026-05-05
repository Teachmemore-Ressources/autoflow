---
title: AWX API
---

# AWX API

L'API AWX est une API REST complète. La documentation interactive est disponible à `https://awx.<DOMAIN>/api/v2/`.

---

## Base URL

```
https://awx.<DOMAIN>/api/v2/
```

---

## Endpoints essentiels

### Ping (santé)

```http
GET /api/v2/ping/
```

```bash
curl -u admin:<PASS> https://awx.<DOMAIN>/api/v2/ping/
```

Réponse :
```json
{
  "ha": false,
  "version": "23.x.x",
  "active_node": "awx",
  "install_uuid": "..."
}
```

---

### Jobs

#### Lister les jobs

```http
GET /api/v2/jobs/
```

Paramètres :

| Paramètre | Type | Description |
|---|---|---|
| `status` | string | `pending`, `running`, `successful`, `failed`, `canceled` |
| `page_size` | int | Nombre de résultats (max 200) |
| `order_by` | string | Champ de tri (`-id` = décroissant) |
| `job_template` | int | Filtrer par template ID |

```bash
# Jobs en cours
curl -u admin:<PASS> "https://awx.<DOMAIN>/api/v2/jobs/?status=running&page_size=10"

# Jobs échoués aujourd'hui
curl -u admin:<PASS> "https://awx.<DOMAIN>/api/v2/jobs/?status=failed&order_by=-id&page_size=20"
```

#### Détail d'un job

```http
GET /api/v2/jobs/{id}/
```

```bash
curl -u admin:<PASS> "https://awx.<DOMAIN>/api/v2/jobs/42/"
```

#### Stdout d'un job

```http
GET /api/v2/jobs/{id}/stdout/?format=txt
```

```bash
curl -u admin:<PASS> "https://awx.<DOMAIN>/api/v2/jobs/42/stdout/?format=txt"
```

#### Annuler un job

```http
POST /api/v2/jobs/{id}/cancel/
```

```bash
curl -X POST -u admin:<PASS> "https://awx.<DOMAIN>/api/v2/jobs/42/cancel/"
```

#### Re-lancer un job

```http
POST /api/v2/jobs/{id}/relaunch/
```

```bash
curl -X POST -u admin:<PASS> "https://awx.<DOMAIN>/api/v2/jobs/42/relaunch/" \
  -H "Content-Type: application/json" \
  -d '{}'
```

---

### Job Templates

#### Lister les templates

```http
GET /api/v2/job_templates/
```

```bash
curl -u admin:<PASS> "https://awx.<DOMAIN>/api/v2/job_templates/" \
  | python3 -c "import sys,json; [print(t['id'], t['name']) for t in json.load(sys.stdin)['results']]"
```

#### Lancer un template

```http
POST /api/v2/job_templates/{id}/launch/
```

Body optionnel :
```json
{
  "extra_vars": {"key": "value"},
  "limit": "host1,host2",
  "tags": "tag1,tag2",
  "skip_tags": "tag3",
  "verbosity": 2
}
```

```bash
curl -X POST \
  -u admin:<PASS> \
  "https://awx.<DOMAIN>/api/v2/job_templates/7/launch/" \
  -H "Content-Type: application/json" \
  -d '{"extra_vars": {"env": "production", "version": "1.5.0"}}'
```

Réponse :
```json
{
  "job": 43,
  "ignored_fields": {},
  "id": 43,
  "type": "job",
  "status": "pending",
  "url": "/api/v2/jobs/43/"
}
```

---

### Inventaires

#### Lister les inventaires

```http
GET /api/v2/inventories/
```

#### Détail d'un inventaire

```http
GET /api/v2/inventories/{id}/
```

#### Hôtes d'un inventaire

```http
GET /api/v2/inventories/{id}/hosts/
```

```bash
curl -u admin:<PASS> "https://awx.<DOMAIN>/api/v2/inventories/1/hosts/" \
  | python3 -c "import sys,json; [print(h['name'], h['variables']) for h in json.load(sys.stdin)['results']]"
```

---

### Credentials

#### Lister les credentials

```http
GET /api/v2/credentials/
```

#### Créer un credential

```http
POST /api/v2/credentials/
```

```bash
curl -X POST \
  -u admin:<PASS> \
  "https://awx.<DOMAIN>/api/v2/credentials/" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "SSH Key Production",
    "credential_type": 1,
    "organization": 1,
    "inputs": {
      "username": "deploy",
      "ssh_key_data": "-----BEGIN OPENSSH PRIVATE KEY-----\n...\n-----END OPENSSH PRIVATE KEY-----\n"
    }
  }'
```

---

### Tokens

#### Créer un token

```http
POST /api/v2/tokens/
```

```bash
curl -X POST \
  -u admin:<PASS> \
  "https://awx.<DOMAIN>/api/v2/tokens/" \
  -H "Content-Type: application/json" \
  -d '{"description": "CI/CD Pipeline", "scope": "write"}'
```

#### Lister mes tokens

```http
GET /api/v2/me/tokens/
```

#### Révoquer un token

```http
DELETE /api/v2/tokens/{id}/
```

---

### Métriques Prometheus

```http
GET /api/v2/metrics/
```

```bash
curl -u admin:<PASS> "https://awx.<DOMAIN>/api/v2/metrics/" | grep "^awx_"
```

Métriques disponibles :

| Métrique | Description |
|---|---|
| `awx_jobs_total` | Nombre total de jobs |
| `awx_running_jobs` | Jobs en cours d'exécution |
| `awx_pending_jobs` | Jobs en attente |
| `awx_failed_jobs` | Jobs échoués |
| `awx_instance_capacity` | Capacité de l'instance |

---

### Activity Stream (audit)

```http
GET /api/v2/activity_stream/
```

```bash
curl -u admin:<PASS> "https://awx.<DOMAIN>/api/v2/activity_stream/?page_size=20&order_by=-id" \
  | python3 -c "
import sys,json
for a in json.load(sys.stdin)['results']:
    print(a['timestamp'], a['operation'], a['object1'], a.get('actor', {}).get('username', 'system'))
"
```

---

## Pagination

```bash
# Récupérer toutes les pages
page=1
while true; do
  data=$(curl -s -u admin:<PASS> "https://awx.<DOMAIN>/api/v2/jobs/?page=$page&page_size=100")
  echo "$data" | python3 -c "import sys,json; d=json.load(sys.stdin); [print(j['id'], j['status']) for j in d['results']]"
  next=$(echo "$data" | python3 -c "import sys,json; print(json.load(sys.stdin).get('next','') or '')")
  [ -z "$next" ] && break
  page=$((page + 1))
done
```
