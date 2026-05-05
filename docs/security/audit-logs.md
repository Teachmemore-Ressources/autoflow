---
title: Audit & Logs
---

# Audit & Logs

---

## AWX — Audit trail

AWX enregistre toutes les actions dans sa base de données :

```bash
# Voir l'historique des activités via l'API
curl -u admin:<PASSWORD> \
  "https://awx.<DOMAIN>/api/v2/activity_stream/?page_size=20" \
  | python3 -m json.tool
```

Dans l'UI : **Activity Stream** (icône horloge dans la sidebar).

Informations enregistrées : qui, quoi, quand, depuis quelle IP.

---

## Traefik — Access logs

Traefik enregistre chaque requête HTTP en JSON :

```bash
# Logs access Traefik
docker logs autoflow_traefik 2>&1 | grep "RouterName" | tail -20

# Voir les 404 / 5xx
docker logs autoflow_traefik 2>&1 | python3 -c "
import sys, json
for line in sys.stdin:
    try:
        d = json.loads(line)
        if d.get('DownstreamStatus', 200) >= 400:
            print(d.get('DownstreamStatus'), d.get('RequestPath'))
    except: pass
"
```

---

## Gitea — Audit

Gitea enregistre les push, pull, créations de webhooks, connexions admin :

```bash
# Logs Gitea
docker logs autoflow_gitea --tail=100 2>&1 | grep -i "admin\|push\|webhook"
```

---

## Event Engine — Audit webhook

Chaque webhook reçu et chaque job lancé sont loggés :

```bash
docker logs autoflow_event_engine --tail=50 2>&1
```

Exemple de log :
```json
{"time": "2024-05-01T02:00:00Z", "event": "webhook_received", "source": "github", "action": "push", "ref": "refs/heads/main"}
{"time": "2024-05-01T02:00:01Z", "event": "job_launched", "template_id": 7, "job_id": 42}
```

---

## Centralisation dans Loki

Tous ces logs sont automatiquement collectés par Promtail et disponibles dans Grafana/Loki :

```logql
# Audit AWX
{container_name="autoflow_awx_web"} |= "activity_stream"

# Access logs Traefik avec statut 5xx
{container_name="autoflow_traefik"} | json | DownstreamStatus >= 500

# Webhooks reçus
{container_name="autoflow_event_engine"} |= "webhook_received" | json
```
