---
title: Réponse aux incidents
---

# Réponse aux incidents

---

## Processus de réponse

```
Détection → Triage → Confinement → Diagnostic → Résolution → Post-mortem
```

---

## Étape 1 — Détection

Les incidents sont détectés via :

- **Alertmanager** → notification Slack/email/webhook
- **Grafana** → seuil franchi sur dashboard
- **Signalement utilisateur** → ticket ITSM
- **Monitoring externe** → UptimeRobot, Zabbix

### Vérification immédiate

```bash
# État global en 30 secondes
docker compose ps
curl -sk https://awx.<DOMAIN>/health/ | python3 -m json.tool
curl -sk https://git.<DOMAIN>/api/v1/repos/search?limit=1 | python3 -m json.tool
```

---

## Étape 2 — Triage (qualification P1→P4)

| Symptôme | Niveau |
|---|---|
| AWX complètement inaccessible | P1 |
| Perte de données suspectée | P1 |
| Automatisations ne se lancent plus | P2 |
| Gitea inaccessible | P2 |
| Monitoring/Grafana down | P3 |
| Alerte cert expiry > 7 jours | P3 |
| Dashboard lent | P4 |

---

## Étape 3 — Confinement

Selon le type d'incident :

=== "Service compromis"

    ```bash
    # Isoler le service sans couper les autres
    docker compose stop <service>
    
    # Capturer les logs avant nettoyage
    docker logs <service> > /tmp/incident-$(date +%Y%m%d-%H%M%S)-<service>.log 2>&1
    ```

=== "Brèche sécurité suspectée"

    ```bash
    # Révoquer les tokens compromis immédiatement
    # Dans AWX UI : User → Tokens → Delete
    
    # Changer le token Event Engine
    # Éditer .env : AWX_TOKEN=<nouveau_token>
    docker compose restart event_engine
    
    # Activer le mode maintenance Traefik si nécessaire
    # (couper le trafic entrant)
    ```

=== "Surcharge ressources"

    ```bash
    # Identifier le coupable
    docker stats --no-stream | sort -k3 -rn | head -5
    
    # Limiter temporairement
    docker compose stop awx_task   # désactiver les jobs planifiés
    ```

---

## Étape 4 — Diagnostic

Suivre les guides de dépannage selon le service incriminé :

- [AWX](troubleshooting/awx.md)
- [Gitea](troubleshooting/gitea.md)
- [Monitoring](troubleshooting/monitoring.md)
- [Certificats](troubleshooting/certificates.md)
- [Réseau](troubleshooting/network.md)

### Logs centralisés (Loki)

```logql
# Erreurs critiques toutes sources (dernière heure)
{job=~"docker"} |= "error" | json | level="critical"

# Timeline d'un incident
{job=~"docker"} | json | line_format "{{.container_name}} {{.log}}"
  | __error__=""
```

---

## Étape 5 — Résolution

### Checklist avant déclaration de résolution

- [ ] Service répond aux health checks
- [ ] Logs propres (plus d'erreurs répétées)
- [ ] Jobs AWX se lancent correctement
- [ ] Utilisateurs peuvent accéder aux services
- [ ] Alertes Alertmanager résolues
- [ ] Monitoring opérationnel

### Communication

```
Status: RÉSOLU
Durée: X minutes
Impact: [description]
Cause racine: [résumé]
Actions correctives: [ce qui a changé]
```

---

## Étape 6 — Post-mortem

Pour tout incident P1/P2, rédiger un post-mortem dans les 24h :

```markdown
## Post-mortem — [Date] — [Titre incident]

**Durée de l'incident** : HH:MM → HH:MM (X min)
**Sévérité** : P1 / P2
**Services impactés** : ...

### Timeline
- HH:MM — Première alerte
- HH:MM — Début investigation
- HH:MM — Cause identifiée
- HH:MM — Résolution

### Cause racine
...

### Actions correctives
- [ ] Action 1 (responsable, date)
- [ ] Action 2 (responsable, date)

### Ce qui a bien fonctionné
...

### Ce qui peut être amélioré
...
```

---

## Runbook d'astreinte

### Alerte AWX job failure

```bash
# 1. Identifier le job en échec
curl -u admin:<PASS> "https://awx.<DOMAIN>/api/v2/jobs/?status=failed&page_size=5" \
  | python3 -c "import sys,json; [print(j['id'], j['name'], j['failed']) for j in json.load(sys.stdin)['results']]"

# 2. Lire les logs du job
curl -u admin:<PASS> "https://awx.<DOMAIN>/api/v2/jobs/<ID>/stdout/?format=txt"

# 3. Re-lancer si la cause est résolue
curl -X POST -u admin:<PASS> "https://awx.<DOMAIN>/api/v2/jobs/<ID>/relaunch/"
```

### Alerte certificat expirant

```bash
# Voir les certificats proches de l'expiration
curl -sk https://pki.<DOMAIN>/api/v1/certificates?expiring_soon=true \
  -H "Authorization: Bearer $PKI_JWT_SECRET"

# Renouveler via l'interface PKI
open https://pki.<DOMAIN>
```

### Alerte disque plein

```bash
# Identifier ce qui prend de la place
du -sh /var/lib/docker/volumes/* | sort -h | tail -10
docker system df -v

# Nettoyer les images non utilisées
docker image prune -a --filter "until=168h"

# Nettoyer les logs Docker
find /var/lib/docker/containers -name "*.log" -exec ls -lh {} \; | sort -k5 -rn | head -10
# Tronquer le plus gros log si nécessaire (irréversible)
# > /var/lib/docker/containers/<id>/<id>-json.log
```
