---
title: Dépannage Monitoring
---

# Dépannage Monitoring

---

## Problème 1 — Grafana inaccessible

**Diagnostic** :
```bash
docker compose ps grafana
docker logs autoflow_grafana --tail=30

# Test interne
docker exec autoflow_grafana curl -s http://localhost:3000/api/health
```

**Solutions** :

```bash
# Redémarrage simple
docker compose restart grafana
sleep 15
curl -sf http://localhost:3000/api/health

# Si la base SQLite Grafana est corrompue
docker compose stop grafana
# Restaurer depuis backup ou réinitialiser (perd les dashboards non provisionés)
docker volume rm autoflow_grafana_data
docker compose up -d grafana
```

---

## Problème 2 — Prometheus ne scrape plus une target

**Diagnostic** :
```bash
# Voir toutes les targets et leur statut
curl -sf http://localhost:9090/api/v1/targets \
  | python3 -c "
import sys,json
data = json.load(sys.stdin)
for t in data['data']['activeTargets']:
    print(t['health'], t['labels'].get('job','?'), t['lastError'] or 'OK')
"
```

**Solutions** :

=== "Target 'down' — service inaccessible"

    ```bash
    # Le service ne répond pas sur son port métriques
    # Exemple : AWX
    docker exec autoflow_awx_web curl -s http://localhost:8052/metrics | head -3
    
    # Redémarrer le service
    docker compose restart <service>
    ```

=== "Target 'down' — label job inconnu"

    ```bash
    # Vérifier la config prometheus.yml
    cat prometheus/prometheus.yml | grep -A5 "job_name:"
    
    # Recharger sans redémarrer
    curl -X POST http://localhost:9090/-/reload
    ```

=== "Erreur TLS (certificat interne non reconnu)"

    ```bash
    # Ajouter tls_config dans prometheus.yml
    # scrape_configs:
    #   - job_name: 'gitea'
    #     tls_config:
    #       ca_file: /etc/prometheus/ca.crt
    #     static_configs:
    #       - targets: ['git.<DOMAIN>']
    
    docker compose exec prometheus cat /etc/prometheus/prometheus.yml | grep -A5 "tls_config"
    ```

---

## Problème 3 — Alertes muettes (Alertmanager ne notifie pas)

**Diagnostic** :
```bash
# Vérifier l'état de l'Alertmanager
curl -sf http://localhost:9093/api/v2/status | python3 -m json.tool

# Voir les alertes actives
curl -sf http://localhost:9093/api/v2/alerts | python3 -m json.tool

# Vérifier les silences (alerte peut-être silencée)
curl -sf http://localhost:9093/api/v2/silences | python3 -m json.tool
```

**Solutions** :

=== "Config SMTP/Slack invalide"

    ```bash
    # Tester la config manuellement
    docker compose exec alertmanager amtool check-config /etc/alertmanager/alertmanager.yml
    
    # Envoyer un test
    curl -X POST http://localhost:9093/api/v2/alerts \
      -H "Content-Type: application/json" \
      -d '[{"labels":{"alertname":"TestAlert","severity":"warning"},"annotations":{"summary":"Test"}}]'
    ```

=== "Route de routage manquante"

    ```bash
    # Vérifier que le receiver est défini et la route existe
    docker compose exec alertmanager cat /etc/alertmanager/alertmanager.yml
    
    # Recharger
    curl -X POST http://localhost:9093/-/reload
    ```

---

## Problème 4 — Grafana — dashboard vide / "No data"

**Diagnostic** :
```bash
# Tester directement Prometheus
curl -sf "http://localhost:9090/api/v1/query?query=up" | python3 -m json.tool

# Vérifier la datasource dans Grafana
curl -u admin:<PASS> http://localhost:3000/api/datasources \
  | python3 -c "import sys,json; [print(d['name'], d['url'], d['type']) for d in json.load(sys.stdin)]"
```

**Solutions** :

```bash
# Re-tester la datasource depuis l'UI
# Grafana → Configuration → Data Sources → Prometheus → Save & test

# Si l'URL Prometheus est incorrecte
# URL correcte dans Grafana : http://prometheus:9090 (réseau Docker interne)
# Pas http://localhost:9090
```

---

## Problème 5 — Loki — logs absents dans Grafana

**Diagnostic** :
```bash
# Vérifier que Loki reçoit des logs
curl -sf "http://localhost:3100/loki/api/v1/labels" | python3 -m json.tool

# Vérifier Promtail
docker logs autoflow_promtail --tail=20 | grep -i "error\|warn\|scrape"

# Voir les targets Promtail
curl -sf http://localhost:9080/targets 2>/dev/null | head -30
```

**Solutions** :

=== "Promtail n'accède pas aux logs Docker"

    ```bash
    # Vérifier le montage du socket Docker
    docker inspect autoflow_promtail | grep -A5 "Mounts"
    
    # Doit avoir : /var/run/docker.sock → /var/run/docker.sock
    # Et : /var/lib/docker/containers → /var/lib/docker/containers
    ```

=== "Loki reject les logs (429 — rate limit)"

    ```bash
    docker logs autoflow_loki --tail=20 | grep "429\|rate\|limit"
    
    # Augmenter dans loki-config.yml :
    # limits_config:
    #   ingestion_rate_mb: 16
    #   ingestion_burst_size_mb: 32
    docker compose restart loki
    ```

=== "Datasource Loki incorrecte dans Grafana"

    ```bash
    # URL correcte : http://loki:3100 (réseau interne Docker)
    ```

---

## Problème 6 — OpenTelemetry Collector — traces absentes

```bash
# Vérifier que l'OTel Collector reçoit des données
docker logs autoflow_otel_collector --tail=20

# Test d'envoi manuel (gRPC)
# Nécessite grpcurl installé
grpcurl -plaintext localhost:4317 list

# Vérifier Jaeger
curl -sf http://localhost:16686/api/services | python3 -m json.tool
```

---

## Problème 7 — Métriques AWX absentes

```bash
# Vérifier que AWX expose ses métriques
curl -u admin:<PASS> "https://awx.<DOMAIN>/api/v2/metrics/" | head -20

# Dans Prometheus, vérifier la target AWX
curl -sf "http://localhost:9090/api/v1/query?query=awx_jobs_total" | python3 -m json.tool

# Si absent, vérifier le job Prometheus pour AWX
grep -A10 "awx" prometheus/prometheus.yml
```

---

## Commandes utiles Prometheus

```bash
# Recharger la config (sans redémarrer)
curl -X POST http://localhost:9090/-/reload

# Voir les règles d'alerte actives
curl -sf http://localhost:9090/api/v1/rules | python3 -m json.tool

# Query directe
curl -sf "http://localhost:9090/api/v1/query?query=<METRIC_NAME>" | python3 -m json.tool

# Query sur intervalle de temps
curl -sf "http://localhost:9090/api/v1/query_range?query=up&start=$(date -d '1 hour ago' +%s)&end=$(date +%s)&step=60"
```

## Commandes utiles Grafana

```bash
# Lister les dashboards
curl -u admin:<PASS> http://localhost:3000/api/search | python3 -c \
  "import sys,json; [print(d['uid'], d['title']) for d in json.load(sys.stdin)]"

# Export d'un dashboard
curl -u admin:<PASS> "http://localhost:3000/api/dashboards/uid/<UID>" \
  | python3 -m json.tool > dashboard-backup.json

# Import d'un dashboard
curl -X POST -u admin:<PASS> http://localhost:3000/api/dashboards/import \
  -H "Content-Type: application/json" \
  -d @dashboard-backup.json
```
