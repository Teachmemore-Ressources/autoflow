---
title: ADR-001 — AWX comme moteur d'orchestration
---

# ADR-001 — AWX comme moteur d'orchestration

**Statut** : Accepté  
**Date** : 2024

---

## Contexte

Autoflow devait s'appuyer sur un moteur d'exécution de tâches d'automatisation. Les critères principaux étaient :

- Exécution fiable de playbooks Ansible
- RBAC (contrôle d'accès granulaire)
- Historique et audit des exécutions
- API REST complète pour l'intégration
- Planification (schedules)
- Support des Execution Environments (isolation des dépendances)
- Solution open-source auto-hébergeable

---

## Décision

Utiliser **AWX** (la version upstream open-source d'Ansible Automation Platform) comme moteur central d'orchestration.

---

## Alternatives considérées

### Semaphore (ex-ansible-semaphore)

- ✅ Plus léger en ressources
- ✅ Interface plus simple
- ❌ RBAC limité
- ❌ Pas d'Execution Environments
- ❌ API moins riche
- ❌ Pas de support des credentials AWX-style

### Jenkins

- ✅ Très flexible, écosystème immense
- ❌ Non spécialisé Ansible
- ❌ Configuration complexe pour Ansible
- ❌ Pas d'EE natif
- ❌ Pas d'inventaire Ansible intégré

### Rundeck

- ✅ Interface opérateur orientée
- ✅ RBAC correct
- ❌ Pas natif Ansible
- ❌ Modèle de licensing commercial pour les features avancées
- ❌ Communauté plus petite

### Scripts cron + Ansible CLI

- ✅ Zéro overhead
- ❌ Pas d'historique, pas d'audit
- ❌ Pas de RBAC
- ❌ Pas de parallélisation gérée
- ❌ Pas d'interface utilisateur

---

## Conséquences

**Avantages** :

- API AWX mature avec documentation exhaustive
- RBAC complet avec organisations, équipes, rôles
- Execution Environments : isolation complète des dépendances Python/collections
- Historique complet des jobs avec stdout
- Inventaires dynamiques et statiques
- Intégration Prometheus native (`/api/v2/metrics/`)
- Activity Stream pour l'audit complet
- Schedules Cron avec gestion des exceptions

**Inconvénients** :

- Ressources importantes (PostgreSQL + Redis + plusieurs workers)
- Complexité de la stack (awx_web, awx_task, awx_rsyslog, awx_postgres)
- Migration vers une nouvelle version d'AWX peut nécessiter une downtime
- La SECRET_KEY ne peut jamais changer sans recréer la DB

**Mitigations** :

- PostgreSQL partagé avec PKI pour réduire l'empreinte
- Sauvegardes automatiques Restic incluant le dump PostgreSQL AWX
- Procédure de mise à jour documentée dans le runbook
