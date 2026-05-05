---
title: Gestion des secrets
---

# Gestion des secrets

Autoflow utilise **SOPS + Age** pour chiffrer le fichier `.env`. Le fichier chiffré `.env.enc` peut être commité en git — sans la clé Age, il est illisible.

---

## Architecture SOPS + Age

```
.env (clair)
    │
    ▼ sops --encrypt
.env.enc (chiffré avec la clé publique Age)
    │
    ├── commité dans git ✅
    │
    ▼ sops --decrypt (nécessite la clé privée Age)
.env (clair)
```

**Age** est un système de chiffrement moderne, simple et auditable. Chaque serveur/membre de l'équipe a sa propre paire de clés.

---

## Clé Age — cycle de vie

### Génération (une seule fois par serveur)

```bash
mkdir -p ~/.config/sops/age
age-keygen -o ~/.config/sops/age/keys.txt
# Affiche la clé publique : age1xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### Structure du fichier clé

```
# created: 2024-05-01T00:00:00+00:00
# public key: age1wn3csx59ga8kppeznnqrq08g42n7h44d6sx4r6tq2p3z9gqvfglq0v46s7
AGE-SECRET-KEY-1XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
```

!!! danger "La clé privée ne quitte jamais le serveur"
    Ne jamais committer ni envoyer par email la ligne `AGE-SECRET-KEY-...`.  
    La clé **publique** (`age1...`) est safe — elle va dans `.sops.yaml`.

---

## Configuration SOPS — `.sops.yaml`

```yaml
creation_rules:
  - path_regex: \.env(\..*)?$
    age: age1wn3csx59ga8kppeznnqrq08g42n7h44d6sx4r6tq2p3z9gqvfglq0v46s7
    input_type: dotenv
    output_type: dotenv
```

La clé publique dans `.sops.yaml` est la clé du **serveur de production**. Pour ajouter un membre de l'équipe, voir section ci-dessous.

---

## Commandes SOPS

```bash
# Chiffrer .env → .env.enc
make secrets-encrypt
# ou: sops --encrypt --input-type dotenv --output-type dotenv .env > .env.enc

# Déchiffrer .env.enc → .env
make secrets-decrypt
# ou: sops --decrypt --input-type dotenv --output-type dotenv .env.enc > .env

# Éditer directement les secrets chiffrés (ouvre $EDITOR)
make secrets-edit
# ou: sops --input-type dotenv --output-type dotenv .env.enc

# Vérifier l'intégrité
make secrets-check
```

---

## Ajouter un membre de l'équipe

Chaque membre génère sa propre clé Age sur sa machine :

```bash
# Sur la machine du nouveau membre
mkdir -p ~/.config/sops/age
age-keygen -o ~/.config/sops/age/keys.txt
# Communiquer uniquement la clé publique : age1...
```

Ajouter sa clé publique dans `.sops.yaml` :
```yaml
creation_rules:
  - path_regex: \.env(\..*)?$
    age: >-
      age1wn3csx59ga8kppeznnqrq08g42n7h44d6sx4r6tq2p3z9gqvfglq0v46s7,
      age1NOUVEAU_MEMBRE_CLE_PUBLIQUE
    input_type: dotenv
    output_type: dotenv
```

Re-chiffrer avec toutes les clés :
```bash
sops updatekeys .env.enc
git add .sops.yaml .env.enc
git commit -m "chore: add team member key"
```

---

## Rotation des secrets

### Secrets à rotation facile

Ces secrets peuvent être changés sans impact majeur :

| Secret | Procédure |
|---|---|
| `POSTGRES_PASSWORD` | Changer dans `.env` + `ALTER USER awx PASSWORD 'new'` dans psql + restart AWX |
| `REDIS_PASSWORD` | Changer dans `.env` + restart Redis + AWX |
| `GRAFANA_ADMIN_PASSWORD` | Changer dans `.env` + restart Grafana ou via UI |
| `JWT_SECRET_KEY` | Changer dans `.env` + restart API (invalide tokens actifs) |
| `GITEA_ADMIN_PASSWORD` | Changer via Gitea UI + `.env` |

### Secrets à NE JAMAIS changer après init

!!! danger "Secrets immuables"
    | Secret | Raison |
    |---|---|
    | `AWX_SECRET_KEY` | Chiffre les credentials AWX — les invalide tous si changé |
    | `GITEA_SECRET_KEY` | Chiffre les données Gitea — corruption si changé |
    | `BACKUP_RESTIC_PASSWORD` | Changer sans ré-initialiser le repo = perte des backups |
    | `GITEA_INTERNAL_TOKEN` | Token interne Gitea — ne pas toucher |

### Procédure de rotation complète (incident sécurité)

En cas de compromission du fichier `.env` :

```bash
# 1. Arrêter la stack
make stop

# 2. Régénérer TOUS les secrets via le wizard
make wizard
# → cliquer "Générer" sur tous les champs, sauvegarder

# 3. Recréer les utilisateurs DB (PostgreSQL)
docker compose up -d postgres
docker exec -i autoflow_postgres psql -U awx -c \
  "ALTER USER awx PASSWORD '$NEW_POSTGRES_PASSWORD';"

# 4. Redémarrer
make start

# 5. Re-chiffrer et commiter
make secrets-encrypt
git add .env.enc && git commit -m "security: rotate all secrets"
```

---

## Secrets à sauvegarder hors du serveur

| Item | Criticité | Où stocker |
|---|---|---|
| Clé Age privée (`keys.txt`) | 🔴 Critique | Gestionnaire de mots de passe + coffre physique |
| `BACKUP_RESTIC_PASSWORD` | 🔴 Critique | Gestionnaire de mots de passe séparé du serveur |
| `.env.enc` | 🟡 Important | Git (déjà versionné) |
| `AWX_SECRET_KEY` | 🟡 Important | Dans `.env.enc` (ne pas noter séparément) |

!!! tip "Règle des 3 copies"
    Backup de la clé Age : 3 copies, 2 supports différents, 1 hors site.
