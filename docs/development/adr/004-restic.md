---
title: ADR-004 — Restic pour les sauvegardes
---

# ADR-004 — Restic pour les sauvegardes

**Statut** : Accepté  
**Date** : 2024

---

## Contexte

Autoflow stocke des données critiques :

- Base de données AWX (jobs, credentials chiffrés, inventaires)
- Dépôts Git Gitea (playbooks, configuration)
- Certificats PKI et base de données PKI
- Dashboards Grafana provisionés

Une solution de sauvegarde devait couvrir ces données avec :

- Sauvegardes incrémentales (pas de full à chaque fois)
- Chiffrement des données au repos
- Support de multiples backends (S3, SFTP, local)
- Rétention configurable (GFS)
- Vérification d'intégrité intégrée
- Restauration granulaire

---

## Décision

Utiliser **Restic** pour les sauvegardes.

---

## Alternatives considérées

### BorgBackup

- ✅ Déduplication efficace
- ✅ Compression
- ✅ Chiffrement
- ❌ Pas de backend S3 natif (nécessite borgmatic + rclone)
- ❌ Montage FUSE pour la restauration granulaire
- ❌ Pas de support Windows

### Velero (Kubernetes)

- ❌ Conçu pour Kubernetes — inutilisable sur Docker Compose
- Non applicable

### Duplicati

- ✅ Interface web
- ✅ Multi-backend
- ❌ Historique de bugs de corruption de backups
- ❌ Pas adapté aux scripts headless
- ❌ Base SQLite qui peut se corrompre

### rsync + cron

- ✅ Simple
- ✅ Pas de dépendance
- ❌ Pas de déduplication
- ❌ Pas de chiffrement natif
- ❌ Pas de gestion de rétention
- ❌ Chaque backup = copie complète (espace disque)

### Velero + Kasten K10

- Non applicable (pas de Kubernetes)

---

## Conséquences

**Avantages** :

- **Déduplication cross-snapshots** : seules les données modifiées sont stockées
- **Chiffrement AES-256 natif** avec une passphrase
- **Backends supportés** : S3/MinIO, SFTP, B2, Azure, GCS, local, REST
- **Politique de rétention GFS** : `forget --keep-hourly N --keep-daily N --keep-weekly N --keep-monthly N`
- Vérification d'intégrité : `restic check`
- Restauration granulaire par fichier ou répertoire
- Single binary, pas de démon
- Performances excellentes en Go

**Inconvénients** :

- Pas d'interface web native (CLI uniquement)
- La variable `BACKUP_CRON` doit être **quotée** dans `.env` (espace dans la valeur cron)
- Les snapshots s'accumulent si `forget --prune` n'est pas exécuté régulièrement

**Mitigations** :

- Le Deploy Wizard installe automatiquement le cron avec la bonne syntaxe
- La politique GFS est configurée avec des valeurs raisonnables par défaut
- Un job de vérification hebdomadaire est planifié (`restic check`)
