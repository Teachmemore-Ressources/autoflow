---
title: Modèle de menaces
---

# Modèle de menaces

---

## Surface d'attaque

| Surface | Exposition | Mesure |
|---|---|---|
| Traefik :443 | Internet/LAN | TLS + routing strict |
| Traefik :80 | Internet/LAN | Redirect HTTPS uniquement |
| Gitea SSH :2222 | Internet/LAN | Clés SSH uniquement |
| Deploy Wizard :9000 | Localhost uniquement | WIZARD_TOKEN + tunnel SSH |
| Ports internes Docker | Réseau autoflow_net uniquement | Pas exposés sur l'hôte |

---

## Risques principaux et mesures

| Risque | Probabilité | Mesure en place |
|---|---|---|
| Fuite de `.env` | Moyenne | SOPS + Age, `.gitignore` |
| Accès non autorisé AWX | Faible | RBAC, HTTPS, CSRF protection |
| CVE dans les images Docker | Continue | Trivy scan automatique |
| Perte de données | Faible | Restic backup chiffré, GFS |
| Certificat expiré | Faible | Alerte Prometheus 30j avant |
| Compromission clé Age | Très faible | Clé hors ligne, rotation possible |

---

## Principe du moindre privilège

- **Gitea registry token** : utilisé par AWX uniquement pour pull les EE — pas pour push
- **Loki S3 account** : accès readwrite sur `loki-chunks` uniquement
- **MONITORING_ADMIN** : accès en lecture seule à Prometheus/Alertmanager
- **Promtail** : bind-mount `/var/lib/docker/containers` en **read-only**

---

## Ce qui n'est PAS sécurisé (améliorations futures)

!!! warning "Points d'amélioration"
    - `ALLOWED_HOSTS = ['*']` par défaut → le wizard dérive maintenant la valeur correcte
    - Pas de WAF (Web Application Firewall) devant Traefik
    - Pas de 2FA AWX (peut être activé dans les paramètres AWX)
    - Pas de rate limiting sur les webhooks Event Engine (DEDUP_TTL aide partiellement)
