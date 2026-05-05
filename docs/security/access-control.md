---
title: Contrôle d'accès
---

# Contrôle d'accès

---

## AWX — RBAC

AWX dispose d'un RBAC complet. Niveaux d'accès :

| Rôle | Accès |
|---|---|
| **System Administrator** | Accès total |
| **System Auditor** | Lecture seule sur tout |
| **Organization Admin** | Admin d'une organisation |
| **Project Admin** | Gestion des projets |
| **Job Template Execute** | Lancer des jobs uniquement |

Créer les utilisateurs dans AWX : **Users → Add**.

---

## Tokens AWX

Les tokens AWX permettent l'accès API sans mot de passe. Scopes disponibles :

- **Read** : lecture seule
- **Write** : lancer des jobs, modifier des ressources

```bash
# Créer un token via l'API
curl -X POST https://awx.<DOMAIN>/api/v2/tokens/ \
  -u admin:<AWX_ADMIN_PASSWORD> \
  -H "Content-Type: application/json" \
  -d '{"description": "Event Engine", "scope": "write"}'
```

---

## Gitea — Tokens d'accès

Dans Gitea : **User Settings → Applications → Generate New Token**

Types de tokens :
- **Personal Access Token** : accès complet au compte
- **Fine-grained token** (Gitea 1.22+) : permissions par repository/scope

Le token `GITEA_REGISTRY_TOKEN` est utilisé par AWX pour pull les images EE.  
Le token `GITEA_METRICS_TOKEN` est utilisé par Prometheus pour scraper les métriques.

---

## Accès SSH Gitea

```bash
# Ajouter une clé SSH dans Gitea : User Settings → SSH Keys
# Puis cloner via SSH :
git clone ssh://git@git.<DOMAIN>:2222/admin-gitea/network-playbooks.git
```

---

## Rotation d'un token compromis

```bash
# AWX : supprimer le token dans AWX UI → User → Tokens
# Puis recréer et mettre à jour .env :
AWX_TOKEN=<new_token>

# Redémarrer l'Event Engine
docker compose restart event_engine
```
