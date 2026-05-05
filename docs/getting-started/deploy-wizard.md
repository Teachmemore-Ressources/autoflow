---
title: Deploy Wizard
---

# Deploy Wizard

Le Deploy Wizard est l'interface graphique de configuration d'Autoflow. Il guide l'opérateur à travers toutes les sections de configuration, génère les secrets, effectue les vérifications pré-déploiement et orchestre le démarrage de la stack.

---

## Lancement

```bash
cd autoflow

# Générer un token d'accès (à chaque session)
export WIZARD_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
echo "Token: $WIZARD_TOKEN"

# Lancer le wizard
make wizard
```

Le wizard démarre sur `http://localhost:9000` (accessible uniquement depuis le serveur).

**Depuis votre poste :**
```bash
ssh -L 9000:localhost:9000 user@<IP_SERVEUR>
# Puis naviguer sur : http://localhost:9000
```

!!! info "Authentification"
    HTTP Basic Auth — login : `admin`, mot de passe : valeur de `$WIZARD_TOKEN`.

---

## Interface

### Navigation latérale

La sidebar liste toutes les sections de configuration dans l'ordre recommandé :

| Section | Contenu |
|---|---|
| **Système** | Utilisateur et mot de passe sudo |
| **Infrastructure** | Domaine, ports Traefik |
| **PostgreSQL** | Credentials base de données AWX |
| **Redis** | Credentials cache/queue |
| **AWX** | Admin, secret key, token, ALLOWED_HOSTS |
| **Autoflow API** | Secret key, JWT, CORS, rate limit |
| **Monitoring** | Grafana, Prometheus, Loki |
| **MinIO** | Stockage objet (backend Loki) |
| **Event Engine** | Tokens, webhooks, notifications |
| **Gitea** | Admin, DB, secrets, registry |
| **PKI** | Admin, JWT, URL |
| **Exec Environments** | Version, build, registry |
| **Advanced** | Log level, scan interval |
| **Backup & DR** | Restic, backend, rétention, cron |
| **Compliance** | Token admin scanner |
| **Pre-flight** | Vérifications avant déploiement |

---

## Sections en détail

### Système

Configure l'utilisateur Linux qui exécutera les opérations de déploiement (scripts, commandes sudo).

| Champ | Variable | Description |
|---|---|---|
| Utilisateur système | `DEPLOY_USER` | Nom d'utilisateur Linux |
| Mot de passe sudo | `SUDO_PASSWORD` | Requis pour PKI, CA Docker, /etc/hosts |

### Infrastructure

Le champ **Domain** est le plus important. Il déclenche la dérivation automatique de tous les sous-domaines et URLs.

| Champ | Variable | Exemple |
|---|---|---|
| Domain | `DOMAIN` | `client.example.com` |
| HTTP Port | `TRAEFIK_HTTP_PORT` | `80` |
| HTTPS Port | `TRAEFIK_HTTPS_PORT` | `443` |
| Gitea SSH Port | `GITEA_SSH_PORT` | `2222` |

**Champs auto-dérivés depuis Domain :**
- `GITEA_DOMAIN` → `client.example.com`
- `GITEA_ROOT_URL` → `https://git.client.example.com`
- `PKI_BASE_URL` → `https://pki.client.example.com`
- `CORS_ORIGINS` → `https://awx.client.example.com,...`
- `AWX_ALLOWED_HOSTS` → `awx.client.example.com,localhost,awxweb`

### Génération automatique de secrets

Les champs marqués avec le bouton **"Générer"** peuvent être auto-générés :

- `hex32` — 32 octets hexadécimaux (64 caractères)
- `hex64` — 64 octets hexadécimaux (128 caractères)
- `urlsafe32` — 32 octets URL-safe base64

!!! tip "Bonne pratique"
    Cliquez **"Générer"** sur **tous** les champs qui le proposent. Ne réutilisez jamais des secrets entre installations client.

### Section Pre-flight

La section Pre-flight effectue des vérifications de l'environnement avant le déploiement :

#### ✅ Permissions & Prérequis système

Vérifie :
- Scripts `scripts/` exécutables (`chmod +x`)
- Répertoire `traefik/certs/` accessible en lecture/écriture
- L'utilisateur est dans le groupe `docker`
- Le fichier `.env` est writable

Le bouton **"Fix permissions"** corrige automatiquement les problèmes courants.

#### 🌐 EE Container DNS

Vérifie la configuration DNS des conteneurs Execution Environment :

- **Statut rapide** (au chargement) : indique si `EE_DNS_SERVER` est vide (recommandé) ou défini
- **"Detect DNS"** (bouton) : teste chaque serveur DNS depuis le réseau bridge Docker avec logs en temps réel

Un terminal s'affiche avec les résultats ligne par ligne :
```
🔍 Lecture des serveurs DNS upstream du système hôte...
   → 192.168.1.254  (lu depuis /run/systemd/resolve/resolv.conf)
   → 1.1.1.1  (fallback public)
🐳 Image alpine présente en cache local
🌐 Test depuis le réseau bridge Docker...
   ✅ 192.168.1.254     répond depuis bridge (galaxy.ansible.com OK)
   ❌ 10.0.2.3          timeout ou NXDOMAIN depuis bridge
✅ EE_DNS_SERVER est vide — Docker gère automatiquement
```

!!! success "Configuration recommandée"
    Laisser `EE_DNS_SERVER` **vide**. Docker génère automatiquement le `resolv.conf` des conteneurs EE à partir des DNS upstream du système. Forcer un serveur spécifique uniquement si Docker ne peut pas accéder aux DNS système depuis le bridge.

#### 🔐 Certificats & PKI

Vérifie que la PKI interne est générée et que les certificats Traefik sont valides.

#### ⚙️ Runner Gitea Actions

Vérifie que `act_runner` est enregistré dans Gitea :
- Lit `GITEA_RUNNER_TOKEN` depuis `.env`
- Vérifie que le conteneur est `running`
- Scrute les logs pour la ligne `runner registered successfully`

Le bouton **"Register runner"** exécute `make gitea-init-runner`.

---

## Sauvegarder la configuration

Chaque section dispose d'un bouton **"Sauvegarder"**. La configuration est écrite dans le fichier `.env`.

!!! warning "Sauvegarder avant de fermer"
    Les modifications non sauvegardées sont perdues si le wizard est fermé ou redémarré. Sauvegardez après chaque section.

---

## Chiffrer les secrets

Après avoir configuré et sauvegardé toutes les sections, chiffrez le fichier `.env` :

=== "Via le wizard"
    Section **Système** → bouton **"Chiffrer .env"**

=== "Via make"
    ```bash
    make secrets-encrypt
    ```

Le fichier `.env.enc` est généré. Vous pouvez le committer en git sans risque :
```bash
git add .env.enc
git commit -m "chore: update encrypted secrets"
```

!!! danger "Ne jamais committer .env"
    Le `.gitignore` exclut `.env` par défaut. Ne le commitez jamais — il contient tous vos secrets en clair.

---

## Déchiffrer les secrets (nouveau serveur ou après restore)

```bash
# Déchiffre .env.enc → .env (nécessite la clé Age dans ~/.config/sops/age/keys.txt)
make secrets-decrypt

# Ou via le wizard : Section Système → "Déchiffrer .env"
```

---

## Commandes SOPS avancées

```bash
# Voir et modifier les secrets chiffrés (éditeur inline)
make secrets-edit

# Vérifier l'intégrité du fichier chiffré
make secrets-check

# Ajouter un nouveau membre de l'équipe (sa clé Age publique)
# 1. Ajouter la clé publique dans .sops.yaml (section age:)
# 2. Re-chiffrer avec toutes les clés
sops updatekeys .env.enc
```

---

## Arrêter le wizard

Le wizard est un processus en premier plan. Pour l'arrêter :
```
Ctrl+C
```

Il peut être relancé à tout moment avec `make wizard` (avec un nouveau `WIZARD_TOKEN`).

---

## Logs du wizard

Les logs du wizard sont écrits dans `/tmp/wizard.log` quand il est lancé en arrière-plan, ou affichés dans le terminal courant.

```bash
# Si lancé en arrière-plan
tail -f /tmp/wizard.log

# Audit des actions du wizard (sauvegarde .env, actions)
cat wizard-audit.log
```
