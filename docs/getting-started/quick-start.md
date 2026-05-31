---
title: Quick Start
---

# Quick Start

Déploiement complet en 4 étapes. Durée : **10 minutes** (hors temps de pull des images Docker — prévoir 15-30 min supplémentaires selon la connexion).

!!! warning "Prérequis"
    Assurez-vous d'avoir complété la page [Prérequis](prerequisites.md) avant de continuer.

---

## Étape 1 — Lancer le Deploy Wizard

Le wizard est l'interface de configuration. Il génère le `.env`, vérifie l'environnement et lance la stack.

### Sur le serveur

```bash
cd autoflow

# Générer un token d'accès sécurisé
export WIZARD_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

# Afficher le token (à noter pour l'accès navigateur)
echo "WIZARD_TOKEN=$WIZARD_TOKEN"

# Lancer le wizard
make wizard
```

Le wizard démarre sur `http://localhost:9000`.

### Depuis votre machine locale

Ouvrez un **second terminal** sur votre poste et créez un tunnel SSH :

```bash
# Remplacer 'user' et '<IP_SERVEUR>' par vos valeurs
ssh -L 9000:localhost:9000 user@<IP_SERVEUR>
```

Ouvrez ensuite votre navigateur sur **`http://localhost:9000`**.

!!! info "Authentification"
    Le wizard demande un login. Entrez :
    - **Utilisateur** : `admin` (ou n'importe quelle valeur)
    - **Mot de passe** : la valeur de `WIZARD_TOKEN` affichée à l'étape précédente

---

## Étape 2 — Configurer via le wizard

Le wizard est divisé en sections. Voici l'ordre recommandé :

### 2.1 Accepter les CGU

À la première connexion, une page de conditions générales d'utilisation s'affiche. Lisez-la et acceptez pour continuer.

### 2.2 Section Système

| Champ | Valeur | Notes |
|---|---|---|
| Utilisateur système | `vagrant` (ou votre user) | L'utilisateur qui lance les commandes |
| Mot de passe sudo | votre mot de passe | Requis pour les opérations privilégiées |

### 2.3 Section Infrastructure

| Champ | Exemple | Notes |
|---|---|---|
| Domain | `client.example.com` | Domaine de base — tous les sous-domaines en découlent |
| HTTP Port | `80` | Laisser par défaut |
| HTTPS Port | `443` | Laisser par défaut |

!!! tip "Domaine LAN"
    En lab ou chez un client sans DNS public, utilisez un domaine `.lan` :
    `autoflow.client.lan`. Configurez le DNS ou `/etc/hosts` en conséquence.

### 2.4 Sections PostgreSQL, Redis, AWX, Gitea, PKI

Cliquer **"Générer"** sur chaque champ marqué `🔑 auto-générable`. Le wizard génère des secrets cryptographiquement forts.

Les champs **obligatoires** (marqués d'un `*`) doivent être renseignés :

- `AWX_ADMIN_PASSWORD` — mot de passe admin AWX
- `GITEA_ADMIN_PASSWORD` — mot de passe admin Gitea
- `GRAFANA_ADMIN_PASSWORD` — mot de passe admin Grafana

### 2.5 Pre-flight checks

La section **Pre-flight** vérifie l'environnement avant le déploiement :

- ✅ **Permissions** — scripts exécutables, docker group, .env writable
- ✅ **EE Container DNS** — DNS accessible depuis le réseau bridge Docker
- ✅ **PKI / Certificats** — CA générée, certificats Traefik valides
- ✅ **Runner Gitea Actions** — runner enregistré et opérationnel

### 2.6 Sauvegarder la configuration

Cliquer **"Sauvegarder"** en bas de chaque section. La configuration est écrite dans le fichier `.env`.

---

## Étape 2.7 — Préparer le host (si pas encore fait)

!!! warning "À faire avant le premier `make start`"
    Si vous n'avez pas encore exécuté `make host-setup` sur ce serveur, faites-le maintenant. Cette commande est idempotente.

```bash
make host-setup
```

Elle configure les paramètres kernel requis (Redis AOF, inotify Gitea, réseau, swap) et les persiste dans `/etc/sysctl.d/10-autoflow.conf`. Voir [Prérequis — Paramètres kernel](prerequisites.md#8-parametres-kernel-make-host-setup) pour le détail.

---

## Étape 3 — Déployer la stack

Une fois la configuration sauvegardée :

```bash
# Dans le terminal serveur (pas celui du tunnel SSH)
make start
```

Equivalent à `docker compose up -d` — démarre tous les services en arrière-plan.

### Vérifier le démarrage

```bash
# Voir l'état de tous les conteneurs
make status

# Suivre les logs AWX (premier démarrage : 2-5 minutes)
make logs SERVICES=awx_web
```

AWX est prêt quand vous voyez :
```
autoflow_awx_web | AWX Server is now running
```

---

## Étape 4 — Initialisation post-démarrage

Une fois AWX démarré, deux initialisations sont nécessaires :

### 4.1 Initialiser le runner Gitea Actions

```bash
make gitea-init-runner
```

Ce script :
1. Attend que Gitea soit disponible
2. Récupère un token d'enregistrement runner
3. Sauvegarde `GITEA_RUNNER_TOKEN` dans `.env`
4. Redémarre `act_runner` avec le nouveau token

### 4.2 Pousser les playbooks réseau dans Gitea

```bash
make gitea-init-network
```

Ce script crée le dépôt `network-playbooks` dans Gitea et pousse les playbooks de base.

---

## Vérification finale

```bash
# État de tous les conteneurs (tous doivent être "Up")
make status

# Accéder aux interfaces
# AWX         : https://awx.<DOMAIN>
# Gitea       : https://git.<DOMAIN>
# Grafana     : https://grafana.<DOMAIN>
# PKI         : https://pki.<DOMAIN>
```

!!! success "Stack opérationnelle"
    Si tous les conteneurs sont `Up (healthy)`, la stack est prête. Passez à la page [Post-installation](post-install.md) pour les configurations finales.

---

## En cas de problème

=== "AWX ne démarre pas"
    ```bash
    # Vérifier les logs de migration
    docker logs autoflow_awx_migrate

    # Vérifier les logs AWX
    make logs SERVICES=awx_web
    ```

=== "Erreur de certificat dans le navigateur"
    ```bash
    # Faire confiance au CA Autoflow
    make docker-trust-ca

    # Ou télécharger le certificat CA depuis :
    # https://pki.<DOMAIN>/ca/download
    # et l'installer dans votre navigateur
    ```

=== "Conteneur unhealthy"
    ```bash
    # Voir les détails d'un conteneur
    docker inspect autoflow_<SERVICE> | grep -A5 Health

    # Voir les logs du conteneur
    docker logs autoflow_<SERVICE> --tail=50
    ```

=== "Port déjà utilisé"
    ```bash
    # Vérifier quel process utilise le port 80 ou 443
    sudo ss -tlnp | grep -E ':80|:443'
    ```
