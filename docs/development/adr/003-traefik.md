---
title: ADR-003 — Traefik comme reverse proxy
---

# ADR-003 — Traefik comme reverse proxy

**Statut** : Accepté  
**Date** : 2024

---

## Contexte

Autoflow expose de nombreux services sur des sous-domaines distincts. Un reverse proxy était nécessaire pour :

- Terminer TLS en un seul point
- Router les requêtes vers les bons containers
- Injecter des headers de sécurité uniformes
- Appliquer le rate limiting et l'authentification
- S'intégrer nativement avec Docker

---

## Décision

Utiliser **Traefik v3** comme reverse proxy et edge router.

---

## Alternatives considérées

### Nginx

- ✅ Ultra-stable, très documenté
- ✅ Hautes performances
- ❌ Configuration statique — nécessite reload à chaque ajout de service
- ❌ Pas de découverte automatique Docker
- ❌ Configuration de Let's Encrypt plus complexe (certbot séparé)
- ❌ Pas d'interface de debug intégrée

### Caddy

- ✅ Configuration très simple (Caddyfile)
- ✅ TLS automatique Let's Encrypt natif
- ✅ Bonne intégration Docker
- ❌ Moins de features avancées (circuit breakers, health checks backend)
- ❌ Middleware ecosystem moins riche
- ❌ Moins adapté aux PKI internes

### HAProxy

- ✅ Performances extrêmes
- ✅ Très stable pour les load balancers haute dispo
- ❌ Pas de découverte Docker native
- ❌ Configuration statique complexe
- ❌ TLS automatique non intégré
- ❌ Sur-dimensionné pour un single-node

### Kong

- ✅ API Gateway complet
- ❌ Très lourd (PostgreSQL requis)
- ❌ Complexité disproportionnée
- ❌ Modèle commercial pour les features avancées

---

## Conséquences

**Avantages** :

- **Découverte automatique Docker** via labels — ajouter un service = ajouter des labels
- **Configuration dynamique** sans reload (hot-reload des fichiers dans `traefik/dynamic/`)
- **TLS automatique** avec Let's Encrypt ou PKI interne (fichiers certs)
- **Dashboard de debug** sur port 8080 (interne)
- Middlewares intégrés : BasicAuth, RateLimit, Headers, Redirect
- Métriques Prometheus natives
- Support HTTP/2 et HTTP/3

**Inconvénients** :

- Moins de documentation en français que Nginx
- La v3 a cassé certaines configurations v2 (migration à gérer)
- Quelques edge cases avec la configuration TLS interne (buffering SSE, etc.)

**Décisions liées** :

- Header `X-Accel-Buffering: no` requis pour les endpoints SSE (Deploy Wizard)
- Tous les services exposés uniquement via Traefik — aucun port direct sur l'hôte sauf 80, 443, 2222
- BasicAuth Traefik pour Prometheus/Alertmanager plutôt qu'un auth intégré
