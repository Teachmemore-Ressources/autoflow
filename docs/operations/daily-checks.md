---
title: Vérifications quotidiennes
---

# Vérifications quotidiennes

Routine de contrôle pour s'assurer qu'Autoflow est opérationnel. Durée : **5 minutes**.

---

## Commande unique — statut global

```bash
make status
```

Tous les conteneurs doivent être `Up` ou `Up (healthy)`. Un conteneur `Exit` ou `unhealthy` nécessite investigation immédiate.

---

## Checklist quotidienne

### 1. État des services

```bash
# Vue d'ensemble
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.RunningFor}}" \
  | grep -v "^NAMES"

# Services critiques uniquement
for svc in autoflow_awx_web autoflow_awx_task autoflow_postgres \
           autoflow_redis autoflow_gitea autoflow_traefik; do
  status=$(docker inspect --format='{{.State.Health.Status}}' $svc 2>/dev/null || \
           docker inspect --format='{{.State.Status}}' $svc 2>/dev/null)
  echo "$svc: $status"
done
```

### 2. Santé des endpoints

```bash
# AWX API
curl -sf https://awx.<DOMAIN>/api/v2/ping/ | python3 -m json.tool

# Autoflow API
curl -sf https://api.<DOMAIN>/health

# Gitea
curl -sf https://git.<DOMAIN>/api/v1/version

# Grafana
curl -sf https://grafana.<DOMAIN>/api/health
```

### 3. Espace disque

```bash
df -h / /var/lib/docker
docker system df
```

!!! warning "Seuils d'alerte"
    - **85%** disque système → nettoyage images inutilisées
    - **90%** disque système → intervention urgente
    - **80%** volume PostgreSQL → vérifier croissance DB

### 4. Derniers jobs AWX

```bash
# Via l'API AWX (remplacer les credentials)
curl -sf -u admin:<PASSWORD> \
  "https://awx.<DOMAIN>/api/v2/jobs/?order_by=-finished&page_size=10" \
  | python3 -m json.tool | grep -E '"status"|"name"'
```

### 5. Alertes actives

Vérifier Alertmanager : `https://alertmanager.<DOMAIN>`

```bash
# Via l'API Alertmanager
curl -sf -u "$MONITORING_ADMIN_USER:$MONITORING_ADMIN_PASSWORD" \
  https://alertmanager.<DOMAIN>/api/v2/alerts \
  | python3 -m json.tool
```

---

## Nettoyage hebdomadaire

```bash
# Supprimer les images Docker non utilisées (libère de l'espace)
docker image prune -f

# Supprimer les volumes non utilisés (ATTENTION — vérifier avant)
docker volume ls -f dangling=true

# Supprimer les conteneurs arrêtés
docker container prune -f

# Voir l'espace récupérable
docker system df
```

---

## Logs des dernières 24h

```bash
# Erreurs dans toute la stack
docker compose logs --since 24h 2>&1 | grep -i "error\|exception\|critical" | tail -50

# Logs d'un service spécifique
make logs SERVICES=awx_task
```

---

## Tableau de bord Grafana — vue d'ensemble

Ouvrir `https://grafana.<DOMAIN>` → **Autoflow Overview** pour une vue consolidée :
- Nombre de jobs AWX (24h) avec taux de réussite
- État des services (UP/DOWN)
- Utilisation ressources (CPU, RAM, disque)
- Alertes actives
