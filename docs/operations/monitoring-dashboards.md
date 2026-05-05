---
title: Dashboards Grafana
---

# Dashboards Grafana

Grafana est accessible sur `https://grafana.<DOMAIN>`. Login : `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD`.

---

## Dashboards préconfigurés

| Dashboard | Accès | Description |
|---|---|---|
| **Autoflow Overview** | Accueil | Jobs AWX, services up/down, alertes actives |
| **AWX Jobs** | Automation | Jobs en cours, taux de réussite, durée |
| **System Host** | Infrastructure | CPU, RAM, disque, réseau hôte |
| **PostgreSQL** | Databases | Connexions, locks, transactions |
| **Redis** | Databases | Mémoire, commandes, evictions |
| **Gitea** | GitOps | Git ops, webhooks, registry |
| **PKI Certificates** | Security | Expiry des certificats, alertes |

---

## Explorer les logs (Loki)

1. **Explore** (icône boussole dans la sidebar)
2. Datasource : **Loki**
3. Requêtes LogQL utiles :

```logql
# Tous les logs d'un service
{container_name="autoflow_awx_web"}

# Erreurs dans toute la stack
{compose_project="autoflow"} |= "error"

# Logs d'un job AWX spécifique
{container_name="autoflow_awx_task"} |= "job_id=42"
```

---

## Explorer les traces (Tempo)

1. **Explore** → Datasource : **Tempo**
2. Rechercher par `trace_id`, service, durée
3. Ou depuis les logs : cliquer sur un `trace_id` pour ouvrir la trace

---

## Alertes actives

**Alerting → Alert rules** — liste toutes les règles et leur état.  
**Alerting → Silences** — silence temporaire d'une alerte (maintenance).
