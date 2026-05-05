---
title: Tracing distribué
---

# Tracing distribué

Les services FastAPI d'Autoflow (API, Event Engine) envoient des traces OpenTelemetry via l'**OTel Collector** vers **Tempo**.

---

## Pipeline

```
Autoflow API :8000  ─┐
                      ├─► OTel Collector :4317 ─► Tempo :3200 ─► Grafana
Event Engine :8001 ─┘
```

Variable d'environnement pour les services : `OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317`

---

## Explorer les traces dans Grafana

1. `https://grafana.<DOMAIN>` → **Explore**
2. Datasource : **Tempo**
3. Requêtes :
   - Par `trace_id` : coller un ID de trace vu dans les logs
   - Par service : `{ resource.service.name="autoflow-api" }`
   - Par durée : filtrer les requêtes lentes > 1s

---

## Corrélation logs ↔ traces

Dans les logs Loki, les entrées JSON incluent `trace_id`. Cliquer dessus dans Grafana ouvre directement la trace correspondante dans Tempo.

---

## Santé de l'OTel Collector

L'OTel Collector utilise une image **distroless** (pas de shell). Vérifier depuis l'extérieur :

```bash
# Health endpoint (JSON)
curl http://localhost:13133/

# Logs du collector
docker logs autoflow_otel_collector --tail=50
```
