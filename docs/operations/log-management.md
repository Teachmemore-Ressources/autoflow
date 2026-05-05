---
title: Gestion des logs
---

# Gestion des logs

Tous les logs des conteneurs Docker sont collectés par **Promtail**, envoyés à **Loki** et stockés dans **MinIO**.

---

## Accéder aux logs

=== "Grafana / Loki (recommandé)"

    `https://grafana.<DOMAIN>` → **Explore** → Datasource : **Loki**

    ```logql
    # Logs d'un service
    {container_name="autoflow_awx_web"} | json

    # Logs d'erreur de toute la stack (24h)
    {compose_project="autoflow"} |= "error" | json
    ```

=== "CLI Docker (temps réel)"

    ```bash
    # Logs d'un service
    make logs SERVICES=awx_web

    # Logs depuis un timestamp
    docker logs autoflow_awx_web --since 1h

    # Recherche dans les logs
    docker logs autoflow_awx_task 2>&1 | grep "ERROR"
    ```

---

## Rétention et espace disque

La rétention est contrôlée par `LOKI_RETENTION` (défaut : `720h` = 30 jours).

```bash
# Espace utilisé par MinIO (logs)
docker exec autoflow_minio df -h /data

# Taille du bucket loki-chunks
docker exec autoflow_minio mc du minio/loki-chunks
```

Pour réduire la rétention (économie d'espace) :
```bash
# Dans .env
LOKI_RETENTION=360h   # 15 jours

# Redémarrer Loki
docker compose restart loki
# Le compacteur nettoie les anciens chunks au prochain cycle
```

---

## LogQL — Référence rapide

```logql
# Filtrer par service
{container_name="autoflow_gitea"}

# Contient une chaîne
{compose_project="autoflow"} |= "webhook"

# Exclut une chaîne
{compose_project="autoflow"} != "health"

# Parser JSON et filtrer sur un champ
{container_name="autoflow_api"} | json | status_code >= 500

# Compter les erreurs par conteneur (rate)
sum by (container_name) (
  rate({compose_project="autoflow"} |= "error" [5m])
)
```
