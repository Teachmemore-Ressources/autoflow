---
title: Architecture Decision Records
---

# Architecture Decision Records (ADR)

Les ADR documentent les décisions architecturales importantes prises lors du développement d'Autoflow — pourquoi une technologie a été choisie, quelles alternatives ont été évaluées, et quels étaient les critères.

---

## Index

| ADR | Titre | Statut |
|---|---|---|
| [ADR-001](001-awx-as-core.md) | AWX comme moteur d'orchestration | Accepté |
| [ADR-002](002-gitea-over-gitlab.md) | Gitea plutôt que GitLab | Accepté |
| [ADR-003](003-traefik.md) | Traefik comme reverse proxy | Accepté |
| [ADR-004](004-restic.md) | Restic pour les sauvegardes | Accepté |
| [ADR-005](005-internal-pki.md) | PKI interne plutôt que Let's Encrypt | Accepté |

---

## Format ADR

Chaque ADR suit le format MADR (Markdown Architecture Decision Records) :

```markdown
## Contexte
Situation et problème que la décision devait résoudre.

## Décision
Ce qui a été décidé.

## Alternatives considérées
Technologies ou approches évaluées et rejetées.

## Conséquences
Avantages et inconvénients de la décision retenue.
```

---

## Créer un nouvel ADR

```bash
# Numéroter séquentiellement
vi docs/development/adr/006-titre-decision.md
```

Ajouter l'entrée dans cet index.
