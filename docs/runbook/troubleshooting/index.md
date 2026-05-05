---
title: Dépannage
---

# Guide de dépannage

Index des problèmes courants par service.

---

## Diagnostic rapide

```bash
# État des containers
docker compose ps

# Dernières erreurs toutes sources
for s in awx_web awx_task traefik gitea event_engine; do
  echo "=== $s ===" 
  docker logs autoflow_$s --tail=5 2>&1 | grep -i "error\|fatal\|critical" || echo "(aucune erreur récente)"
done

# Ressources
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"
```

---

## Index des problèmes

| Symptôme | Guide |
|---|---|
| AWX 502 / jobs qui ne se lancent pas | [Dépannage AWX](awx.md) |
| Gitea inaccessible / push refusé | [Dépannage Gitea](gitea.md) |
| Grafana vide / alertes muettes | [Dépannage Monitoring](monitoring.md) |
| Certificat expiré / HTTPS cassé | [Dépannage Certificats](certificates.md) |
| Service injoignable / DNS cassé | [Dépannage Réseau](network.md) |
