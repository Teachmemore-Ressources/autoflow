---
title: Runbook
---

# Runbook Opérationnel

Ce runbook couvre les procédures d'urgence, la récupération après incident, et le dépannage de chaque composant Autoflow.

---

## Organisation

| Section | Contenu |
|---|---|
| [Réponse aux incidents](incident-response.md) | Niveaux de sévérité, escalade, communication |
| [Récupération des services](service-recovery.md) | Procédures par service |
| [Reprise après désastre](disaster-recovery.md) | Scénario perte totale du serveur |
| [Dépannage — AWX](troubleshooting/awx.md) | 15+ problèmes courants AWX |
| [Dépannage — Gitea](troubleshooting/gitea.md) | Problèmes Gitea & registry |
| [Dépannage — Monitoring](troubleshooting/monitoring.md) | Prometheus, Grafana, alertes |
| [Dépannage — Certificats](troubleshooting/certificates.md) | PKI, TLS, expirations |
| [Dépannage — Réseau](troubleshooting/network.md) | DNS, routing, connectivité |

---

## Principes généraux

### Avant toute intervention

```bash
# 1. Prendre un snapshot de l'état actuel
docker compose ps > /tmp/snapshot-$(date +%Y%m%d-%H%M%S).txt
docker stats --no-stream >> /tmp/snapshot-$(date +%Y%m%d-%H%M%S).txt

# 2. Identifier le service en cause
docker compose ps | grep -v "Up"

# 3. Lire les logs du service incriminé
docker logs <service> --tail=100 2>&1
```

### Commandes de diagnostic rapide

```bash
# Vue d'ensemble santé
docker compose ps

# Ressources CPU/RAM
docker stats --no-stream

# Espace disque
df -h /

# Volumes Docker
docker system df

# Réseau
docker network ls
docker network inspect autoflow_net
```

---

## Niveaux d'urgence

| Niveau | Description | Délai réponse |
|---|---|---|
| **P1 — Critique** | Plateforme inaccessible, data loss imminent | < 15 min |
| **P2 — Majeur** | Service dégradé, automatisations bloquées | < 1h |
| **P3 — Mineur** | Fonctionnalité partielle, impact limité | < 4h |
| **P4 — Informatif** | Alerte non bloquante, maintenance planifiable | Prochain sprint |

---

## Contacts d'urgence

Définir dans votre ITSM ou `docs/contacts.md` (non versionné) :

- Administrateur principal Autoflow
- Backup administrateur
- Équipe infrastructure
- Astreinte réseau/sécurité
