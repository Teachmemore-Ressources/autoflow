---
title: Autoflow Documentation
hide:
  - navigation
  - toc
---

<div class="hero" markdown>
# Autoflow
**Plateforme d'automatisation entreprise — clé en main**

Déployez AWX, Gitea, monitoring complet, PKI interne et Disaster Recovery sur n'importe quel serveur Linux en moins d'une heure.

[Démarrage rapide](getting-started/quick-start.md){ .md-button .md-button--primary }
[Vue d'ensemble](overview/what-is-autoflow.md){ .md-button }
</div>

---

## Ce qu'Autoflow vous donne

<div class="grid-cards" markdown>

<div class="card" markdown>
### ⚙️ AWX — Automation Engine
Ansible Tower open-source. Lancez des playbooks, planifiez des jobs, gérez des inventaires. Interface web complète avec RBAC et audit trail.
</div>

<div class="card" markdown>
### 🦊 Gitea — GitOps & Registry
Git self-hosted + Gitea Actions CI/CD + Container Registry. Vos playbooks, EE et pipelines dans un seul endroit, sur votre infrastructure.
</div>

<div class="card" markdown>
### 📊 Monitoring complet
Prometheus + Grafana + Loki + Tempo + Alertmanager. Métriques, logs, traces distribuées et alertes pour toute la stack, prêt à l'emploi.
</div>

<div class="card" markdown>
### 🔐 PKI interne
Autorité de certification interne. Tous les services HTTPS sans certificats auto-signés suspects. Rotation et révocation centralisées.
</div>

<div class="card" markdown>
### 🔔 Event Engine
Réception de webhooks (GitHub, Alertmanager, Gitea, génériques) → routage vers les job templates AWX. Règles YAML, déduplication, notifications Slack.
</div>

<div class="card" markdown>
### 💾 Disaster Recovery
Backups chiffrés avec Restic (local, SFTP, S3, B2). Politique GFS (daily/weekly/monthly/yearly). RTO/RPO configurables. Restore testé automatiquement.
</div>

<div class="card" markdown>
### 🛡️ Security Scanner
Trivy intégré : scan CVE de toutes les images Docker, rapports de conformité (NIST SP 800-53, CIS Controls v8, ISO 27001, PCI-DSS v4.0, SOC 2).
</div>

<div class="card" markdown>
### 🧙 Deploy Wizard
Interface web de configuration guidée. Génération automatique des secrets, détection DNS, pré-flight checks, déploiement en un clic.
</div>

</div>

---

## Par où commencer ?

| Je veux… | Par ici |
|---|---|
| Comprendre ce qu'est Autoflow | [Vue d'ensemble](overview/what-is-autoflow.md) |
| Voir l'architecture technique | [Architecture](overview/architecture.md) |
| Déployer chez un client | [Prérequis](getting-started/prerequisites.md) → [Quick Start](getting-started/quick-start.md) |
| Utiliser le Deploy Wizard | [Deploy Wizard](getting-started/deploy-wizard.md) |
| Configurer un composant spécifique | [Configuration](configuration/index.md) |
| Répondre à un incident | [Runbook](runbook/incident-response.md) |
| Consulter l'API | [Référence API](api-reference/index.md) |

---

## Stack technique

| Composant | Version | Rôle |
|---|---|---|
| AWX | 24.6.1 | Moteur d'automatisation Ansible |
| Traefik | 2.11 | Reverse proxy + TLS termination |
| Gitea | 1.23 | Git self-hosted + CI/CD + Registry |
| PostgreSQL | 15.17 | Base de données AWX et Gitea |
| Redis | 7.4.8 | Cache et queue AWX |
| Prometheus | 3.x | Collecte de métriques |
| Grafana | 12.x | Visualisation et dashboards |
| Loki | 3.4 | Agrégation de logs |
| Tempo | 2.6 | Tracing distribué |
| MinIO | 2024-10 | Stockage objet (backend Loki) |
| Alertmanager | 0.32 | Gestion des alertes |
| OTel Collector | 0.111 | Pipeline OpenTelemetry |
| Restic | latest | Backup chiffré |
| Trivy | 0.63 | Scanner de vulnérabilités |
