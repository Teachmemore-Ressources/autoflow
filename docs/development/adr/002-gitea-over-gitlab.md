---
title: ADR-002 — Gitea plutôt que GitLab
---

# ADR-002 — Gitea plutôt que GitLab

**Statut** : Accepté  
**Date** : 2024

---

## Contexte

Autoflow nécessite un hébergement Git auto-hébergé pour :

- Stocker les playbooks Ansible et la configuration
- Servir de déclencheur via webhooks
- Héberger un container registry pour les Execution Environments
- Exposer des métriques Prometheus
- S'exécuter dans un contexte air-gapped

Les contraintes étaient la légèreté (serveur single-node), la facilité d'opération, et le faible besoin en ressources.

---

## Décision

Utiliser **Gitea** comme plateforme Git auto-hébergée.

---

## Alternatives considérées

### GitLab CE

- ✅ Fonctionnalités très complètes (CI/CD, Issues, Wiki, Container Registry, LDAP)
- ✅ Large communauté
- ❌ 4-8 GB RAM minimum recommandés
- ❌ Dizaines de composants internes (Puma, Sidekiq, Workhorse, Gitaly, PostgreSQL, Redis...)
- ❌ Temps de démarrage long (2-5 minutes)
- ❌ Complexité opérationnelle disproportionnée pour le use-case
- ❌ Mises à jour fréquentes avec risques de régression

### Gogs

- ✅ Prédécesseur de Gitea, très léger
- ❌ Développement au ralenti
- ❌ Moins de features que Gitea
- ❌ Pas de Gitea Actions intégré
- ❌ Container registry moins mature

### Forgejo

- ✅ Fork communautaire de Gitea, totalement libre
- ✅ Compatible API avec Gitea
- ✅ Légèrement plus actif que Gitea sur certains aspects
- ⚠️ Moins de documentation disponible
- ℹ️ Migration possible depuis Gitea sans friction

### GitHub / GitLab.com (SaaS)

- ✅ Zéro opération
- ❌ Données hors contrôle
- ❌ Incompatible air-gapped
- ❌ Dépendance externe

---

## Conséquences

**Avantages** :

- **< 200 MB RAM** en fonctionnement normal
- Démarrage en **< 5 secondes**
- Single binary, configuration via `app.ini`
- Container registry intégré (packages Gitea)
- **Gitea Actions** : compatible GitHub Actions (même syntaxe YAML)
- API REST complète et documentée
- Webhooks natifs avec signature HMAC-SHA256
- Métriques Prometheus via token dédié
- Support SSH natif (port 2222)

**Inconvénients** :

- Pas d'interface Issues/Kanban aussi avancée que GitLab
- CI/CD moins puissant que GitLab CI
- Pas de gestion LDAP/SSO aussi mature

**Note** : Forgejo reste une alternative viable si les conditions de gouvernance changent. La migration serait transparente grâce à la compatibilité API.
