---
title: Ajouter un service
---

# Ajouter un nouveau service Docker

Guide pour intégrer un nouveau service dans la stack Autoflow.

---

## Checklist d'intégration

- [ ] Définir le service dans `docker-compose.yml`
- [ ] Le rattacher au réseau `autoflow_net`
- [ ] Configurer le routing Traefik (labels ou fichier dynamic)
- [ ] Ajouter les variables d'environnement dans `.env` et le schéma wizard
- [ ] Configurer le scraping Prometheus (si le service expose des métriques)
- [ ] Ajouter la collecte de logs Promtail (si nécessaire)
- [ ] Définir un healthcheck
- [ ] Documenter dans cette doc

---

## Étape 1 — Définir le service dans `docker-compose.yml`

```yaml
services:
  mon_service:
    image: exemple/mon-service:1.0
    container_name: autoflow_mon_service
    restart: unless-stopped
    networks:
      - autoflow_net
    environment:
      - MON_SERVICE_PORT=8080
      - MON_SERVICE_SECRET=${MON_SERVICE_SECRET}
    volumes:
      - mon_service_data:/data
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 15s
    labels:
      # Traefik routing
      - "traefik.enable=true"
      - "traefik.http.routers.mon-service.rule=Host(`mon-service.${DOMAIN}`)"
      - "traefik.http.routers.mon-service.entrypoints=websecure"
      - "traefik.http.routers.mon-service.tls=true"
      - "traefik.http.services.mon-service.loadbalancer.server.port=8080"
      # Middleware auth si nécessaire
      - "traefik.http.routers.mon-service.middlewares=mon-service-auth"

volumes:
  mon_service_data:

networks:
  autoflow_net:
    external: true
```

---

## Étape 2 — Configurer Traefik

### Option A — Labels Docker (simple)

Déjà fait à l'étape 1 via les labels du container.

### Option B — Fichier de config dynamic (avancé)

Pour les services avec des besoins complexes (middlewares, règles custom) :

```yaml
# traefik/dynamic/mon-service.yml
http:
  routers:
    mon-service:
      rule: "Host(`mon-service.{{ env \"DOMAIN\" }}`)"
      entrypoints: [websecure]
      tls: {}
      middlewares: [mon-service-auth, secure-headers]
      service: mon-service-svc

  middlewares:
    mon-service-auth:
      basicAuth:
        users:
          - "admin:$apr1$..."  # htpasswd

  services:
    mon-service-svc:
      loadBalancer:
        servers:
          - url: "http://mon_service:8080"
```

---

## Étape 3 — Variables d'environnement

### Dans `.env`

```bash
# Mon Service
MON_SERVICE_SECRET=<secret_généré>
MON_SERVICE_PORT=8080
```

### Dans le schéma wizard (`services/deploy-wizard/schema.py`)

```python
{
    "key": "MON_SERVICE_SECRET",
    "label": "Secret Mon Service",
    "section": "mon_service",    # nouvelle section ou existante
    "type": "password",
    "required": True,
    "generated": True,           # généré automatiquement
    "description": "Secret d'authentification du service.",
},
```

---

## Étape 4 — Métriques Prometheus

Si le service expose des métriques au format Prometheus (`/metrics`) :

```yaml
# prometheus/prometheus.yml
scrape_configs:
  # ... configs existantes ...

  - job_name: 'mon_service'
    scheme: http
    static_configs:
      - targets: ['mon_service:8080']
    metrics_path: /metrics
    scrape_interval: 30s
```

Recharger Prometheus :
```bash
curl -X POST http://localhost:9090/-/reload
```

---

## Étape 5 — Collecte de logs Loki

Promtail collecte automatiquement les logs Docker de tous les containers dont le nom commence par `autoflow_`. Aucune configuration supplémentaire n'est nécessaire.

Pour ajouter des labels spécifiques :

```yaml
# promtail/promtail-config.yml — ajouter dans scrape_configs
- job_name: mon_service
  docker_sd_configs:
    - host: unix:///var/run/docker.sock
      refresh_interval: 5s
      filters:
        - name: name
          values: [autoflow_mon_service]
  relabel_configs:
    - source_labels: [__meta_docker_container_name]
      target_label: container_name
    - source_labels: [__meta_docker_container_label_com_docker_compose_service]
      target_label: service
```

---

## Étape 6 — Dashboard Grafana

Créer un fichier JSON dans `grafana/dashboards/` :

```json
{
  "__inputs": [],
  "__requires": [],
  "annotations": {},
  "description": "Dashboard Mon Service",
  "panels": [],
  "schemaVersion": 38,
  "tags": ["autoflow", "mon-service"],
  "title": "Mon Service",
  "uid": "mon-service",
  "version": 1
}
```

Le fichier est chargé automatiquement par Grafana au démarrage via le provisioning.

---

## Exemple complet — Intégrer Netbox

Netbox est un outil de gestion d'inventaire réseau. Voici comment l'intégrer :

```yaml
# docker-compose.yml
services:
  netbox_redis:
    image: redis:7-alpine
    container_name: autoflow_netbox_redis
    restart: unless-stopped
    networks: [autoflow_net]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]

  netbox:
    image: netboxcommunity/netbox:latest
    container_name: autoflow_netbox
    restart: unless-stopped
    networks: [autoflow_net]
    depends_on: [netbox_redis]
    environment:
      - ALLOWED_HOSTS=netbox.${DOMAIN}
      - DB_HOST=awx_postgres    # Réutilise le Postgres AWX (base différente)
      - DB_NAME=netbox
      - REDIS_HOST=netbox_redis
      - SECRET_KEY=${NETBOX_SECRET_KEY}
    volumes:
      - netbox_data:/opt/netbox/netbox/media
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/api/"]
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.netbox.rule=Host(`netbox.${DOMAIN}`)"
      - "traefik.http.routers.netbox.entrypoints=websecure"
      - "traefik.http.routers.netbox.tls=true"
      - "traefik.http.services.netbox.loadbalancer.server.port=8080"

volumes:
  netbox_data:
```

---

## Bonnes pratiques

!!! tip "Réseau"
    Toujours rattacher le service à `autoflow_net` — jamais exposer un port directement sur l'hôte.

!!! tip "Secrets"
    Toujours passer les secrets via des variables d'environnement depuis `.env`, jamais en dur dans `docker-compose.yml`.

!!! tip "Healthcheck"
    Définir un healthcheck pour chaque service — Traefik attend que le service soit "healthy" avant de router le trafic.

!!! tip "Volumes nommés"
    Utiliser des volumes Docker nommés (préfixés `autoflow_`) plutôt que des bind mounts — ils sont inclus automatiquement dans les sauvegardes Restic.
    Si le volume contient des **données critiques** (base de données, certificats, dépôts Git), le déclarer `external: true` avec un nom explicite et le créer manuellement (ou via le wizard) pour le protéger d'un `docker compose down -v` accidentel.
