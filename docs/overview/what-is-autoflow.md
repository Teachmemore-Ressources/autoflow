---
title: Qu'est-ce qu'Autoflow ?
---

# Qu'est-ce qu'Autoflow ?

## En une phrase

Autoflow est une **plateforme d'automatisation entreprise clé en main** qui assemble AWX (Ansible Tower open-source), Gitea, un stack de monitoring complet, une PKI interne et un système de Disaster Recovery — le tout déployable sur n'importe quel serveur Linux en moins d'une heure via un wizard de configuration.

---

## Le problème qu'il résout

Déployer AWX en production, c'est aussi devoir gérer :

- Un **dépôt Git** pour stocker les playbooks (Gitea, GitLab, GitHub…)
- Un **pipeline CI/CD** pour tester et livrer les playbooks
- Un **registry Docker** pour les Execution Environments
- Un **reverse proxy** avec TLS valide pour tous les services
- Une **PKI interne** pour éviter les certificats auto-signés
- Un **monitoring** (métriques, logs, alertes) pour savoir quand quelque chose cloche
- Un **système de backup** pour ne pas perdre la configuration AWX en cas de crash
- Un **scanner de sécurité** pour auditer les images et générer des rapports de conformité

La plupart des équipes assemblent tout ça à la main, service par service, avec des configurations qui dérivent au fil du temps et des secrets éparpillés dans des fichiers `.env` non chiffrés.

**Autoflow pré-assemble et préconfigure tout ça.** Un seul `docker compose up` (précédé d'un passage dans le wizard) donne une plateforme opérationnelle et sécurisée.

---

## Cas d'usage typiques

### 1. MSP / Intégrateur système
Vous déployez Autoflow chez chaque client comme socle d'automatisation. Chaque installation est isolée, configurée avec le domaine du client, et la stack est identique d'un client à l'autre. Le wizard génère tous les secrets automatiquement.

### 2. Équipe DevOps interne
Vous avez besoin d'AWX pour orchestrer les déploiements Ansible mais vous ne voulez pas maintenir 10 services séparément. Autoflow vous donne AWX + monitoring + backup + CI/CD dans un seul projet versionné.

### 3. Environnement de formation / lab
Autoflow déploie une plateforme complète et réaliste sur un seul serveur. Idéal pour former des équipes à Ansible, AWX, GitOps et Observability sans infrastructure complexe.

### 4. Projet air-gapped
Autoflow supporte les environnements sans accès internet : les Execution Environments sont buildées localement, les collections Ansible sont pré-installées dans les images, et le registry Gitea stocke tout en interne.

---

## Ce qu'Autoflow n'est PAS

!!! warning "Périmètre"
    - **Pas un service cloud** : Autoflow se déploie sur votre infrastructure. Vous en gardez le contrôle total.
    - **Pas un remplacement d'AWX** : AWX reste le moteur. Autoflow l'intègre et l'enrichit.
    - **Pas une solution haute disponibilité** (dans sa version actuelle) : c'est une architecture single-node. Pour du HA, il faut adapter le `docker-compose.yml` ou passer sur Kubernetes.
    - **Pas un gestionnaire de configuration** : Autoflow ne remplace pas votre IaC (Terraform, Pulumi). Il s'y intègre.

---

## Pour qui ?

| Profil | Usage recommandé |
|---|---|
| Administrateur système | Déploiement et opérations Day-2 |
| Ingénieur DevOps | Intégration CI/CD, écriture de playbooks |
| Architecte | Référence architecture, décisions techniques (ADR) |
| Responsable sécurité | Section Sécurité, rapports de conformité |
| Formateur | Déploiement de labs, reproduction de scénarios |

---

## Modèle de déploiement

```
Un serveur Linux (bare-metal ou VM)
  └── Docker Engine + Docker Compose v2
        └── Autoflow stack (25+ services)
              ├── AWX (moteur Ansible)
              ├── Gitea (Git + CI/CD + Registry)
              ├── Monitoring (Prometheus, Grafana, Loki, Tempo)
              ├── PKI interne
              ├── Event Engine
              ├── Backup (Restic)
              └── Security Scanner (Trivy)
```

Tout le trafic entrant passe par **Traefik** qui termine TLS et route vers le bon service. Les utilisateurs n'accèdent jamais directement aux ports internes.

---

## Open source vs composants propriétaires

Autoflow est entièrement composé de projets open source :

| Composant | Licence | Source |
|---|---|---|
| AWX | Apache 2.0 | Red Hat / Ansible |
| Gitea | MIT | Gitea community |
| Traefik | MIT | Traefik Labs |
| Prometheus | Apache 2.0 | CNCF |
| Grafana | AGPL-3.0 | Grafana Labs |
| Loki | AGPL-3.0 | Grafana Labs |
| Tempo | AGPL-3.0 | Grafana Labs |
| MinIO | AGPL-3.0 | MinIO Inc. |
| Restic | BSD-2 | Alexander Neumann |
| Trivy | Apache 2.0 | Aqua Security |

Le code de colle (scripts, wizard, event engine, API, PKI, EE builder) est développé et maintenu par l'équipe Autoflow.
