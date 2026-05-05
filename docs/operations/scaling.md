---
title: Tuning & Scaling
---

# Tuning & Scaling

---

## Limites de ressources actuelles

Chaque service a des limites Docker Compose définies dans `docker-compose.yml` :

| Service | CPU limit | RAM limit |
|---|---|---|
| awx_web | 1.0 | 1 Go |
| awx_task | 2.0 | 2 Go |
| postgres | 0.5 | 512 Mo |
| redis | 0.25 | 256 Mo |
| gitea | 0.5 | 512 Mo |
| grafana | 0.5 | 512 Mo |
| loki | 0.5 | 512 Mo |
| prometheus | 0.5 | 512 Mo |
| promtail | 0.2 | 128 Mo |

---

## Augmenter les ressources AWX

Pour des environnements avec nombreux jobs simultanés :

```yaml
# docker-compose.yml
awx_task:
  deploy:
    resources:
      limits:
        cpus: '4.0'
        memory: 4096M
```

```bash
docker compose up -d --force-recreate awx_task
```

---

## PostgreSQL — optimisation

```sql
-- Voir les connexions actives
SELECT count(*) FROM pg_stat_activity;

-- Voir les requêtes lentes
SELECT query, mean_exec_time, calls
FROM pg_stat_statements
ORDER BY mean_exec_time DESC LIMIT 10;
```

Variables PostgreSQL à ajuster pour plus de RAM :
```
# Ajouter dans docker-compose.yml sous postgres → command
-c shared_buffers=256MB
-c effective_cache_size=768MB
-c work_mem=4MB
```

---

## Nettoyage Docker

```bash
# Voir l'utilisation
docker system df

# Nettoyer les images non utilisées
docker image prune -f

# Nettoyage complet (ATTENTION : ne supprime pas les volumes nommés)
docker system prune -f
```

---

## Monitoring de la performance

Les métriques de performance sont dans Grafana :
- **System Host** dashboard : CPU, RAM, I/O, réseau
- **PostgreSQL** dashboard : connexions, transactions, cache hit ratio
- **Redis** dashboard : mémoire, evictions, hit rate
