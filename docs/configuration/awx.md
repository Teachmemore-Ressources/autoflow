---
title: Configuration AWX
---

# Configuration AWX

AWX est le moteur d'automatisation Ansible d'Autoflow. Cette page documente la configuration de production définie dans `awx/settings.py`, montée dans les conteneurs `awx_web` et `awx_task` à `/etc/tower/settings.py`.

---

## Fichier de configuration

**Chemin hôte :** `awx/settings.py`  
**Chemin conteneur :** `/etc/tower/settings.py` (lecture seule)

Ce fichier est monté dans les deux conteneurs AWX principaux :

```yaml
# docker-compose.yml (extrait)
awx_web:
  volumes:
    - ./awx/settings.py:/etc/tower/settings.py:ro

awx_task:
  volumes:
    - ./awx/settings.py:/etc/tower/settings.py:ro
```

!!! info "Format Django"
    `settings.py` est un fichier Python Django standard. AWX le charge automatiquement en mode production. Les secrets sont injectés via les variables d'environnement du conteneur — **jamais en clair dans le fichier**.

---

## Base de données

```python
DATABASES = {
    'default': {
        'ATOMIC_REQUESTS': True,
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('POSTGRES_DB', 'awx'),
        'USER': os.environ.get('POSTGRES_USER', 'awx'),
        'PASSWORD': os.environ['POSTGRES_PASSWORD'],   # REQUIRED
        'HOST': os.environ.get('DATABASE_HOST', 'postgres'),
        'PORT': os.environ.get('DATABASE_PORT', '5432'),
    }
}
```

| Variable | Défaut | Description |
|---|---|---|
| `POSTGRES_DB` | `awx` | Nom de la base de données |
| `POSTGRES_USER` | `awx` | Utilisateur PostgreSQL |
| `POSTGRES_PASSWORD` | — | Mot de passe (requis) |
| `DATABASE_HOST` | `postgres` | Hostname du conteneur PostgreSQL |
| `DATABASE_PORT` | `5432` | Port PostgreSQL |

`LISTENER_DATABASES` configure des connexions persistantes pour `pg_notify` (notifications temps réel) avec des paramètres keepalive agressifs pour maintenir la connexion stable sur de longues périodes.

---

## Sécurité Django

```python
SECRET_KEY = os.environ['AWX_SECRET_KEY']

ALLOWED_HOSTS = [h.strip() for h in
    os.environ.get('AWX_ALLOWED_HOSTS', '*').split(',')
    if h.strip()]

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST    = True
CSRF_COOKIE_SECURE      = True
SESSION_COOKIE_SECURE   = True
```

### `AWX_SECRET_KEY`

Clé secrète Django utilisée pour signer les sessions, les tokens CSRF et les données chiffrées (credentials AWX).

!!! danger "Ne jamais changer en production"
    Changer `AWX_SECRET_KEY` **invalide tous les credentials** stockés dans AWX (mots de passe, clés SSH, tokens). Si la clé est compromise, il faut recréer tous les credentials manuellement après rotation.

### `AWX_ALLOWED_HOSTS`

Liste des hostnames acceptés par Django, séparés par des virgules dans `.env` :

```bash
# .env
AWX_ALLOWED_HOSTS=awx.mon-domaine.com,awxweb,localhost
```

En production, le wizard dérive cette valeur automatiquement depuis `DOMAIN`. En cas de `400 Bad Request` à la connexion, vérifiez que le hostname utilisé est dans cette liste.

---

## Redis (Broker & Cache)

```python
BROKER_URL = 'redis://:PASSWORD@redis:6379/0'

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            'hosts': [BROKER_URL],
            'capacity': 10000,
            'group_expiry': 157784760,  # 5 ans
        },
    }
}

CACHES = {
    'default': {
        'BACKEND': 'awx.main.cache.AWXRedisCache',
        'LOCATION': 'redis://:PASSWORD@redis:6379/1',
    }
}
```

Redis est utilisé pour trois rôles :
- **Base 0** — Broker Celery (file de messages pour les tâches AWX)
- **Base 1** — Cache Django
- **Channel Layers** — WebSocket temps réel (streaming des outputs de jobs)

| Variable | Défaut | Description |
|---|---|---|
| `REDIS_HOST` | `redis` | Hostname du conteneur Redis |
| `REDIS_PORT` | `6379` | Port Redis |
| `REDIS_PASSWORD` | — | Mot de passe Redis (requis en production) |

---

## Receptor

```python
RECEPTOR_SOCKET_PATH = '/var/run/receptor/receptor.sock'
```

Le socket Unix est partagé entre les conteneurs `receptor` et `awx_task` via le volume nommé `receptor_run`. AWX_task communique avec Receptor pour lancer les Execution Environments.

---

## WebSocket & Broadcast

```python
BROADCAST_WEBSOCKET_PROTOCOL    = 'http'
BROADCAST_WEBSOCKET_PORT        = 80
BROADCAST_WEBSOCKET_VERIFY_CERT = False
```

Ces paramètres configurent la communication WebSocket entre `awx_task` et `awx_web` pour le streaming temps réel des outputs de jobs. Traefik termine TLS — en interne la communication est HTTP. `BROADCAST_WEBSOCKET_PORT=80` est **critique** : la valeur par défaut AWX (443) casserait le broadcast sans TLS interne.

!!! warning "Streaming absent ou figé"
    Si les outputs de jobs ne s'affichent pas en temps réel dans l'interface AWX, vérifiez que `BROADCAST_WEBSOCKET_PORT=80` est bien défini dans `awx/settings.py` et redémarrez `awx_web` et `awx_task`.

---

## URL de base et redirections

```python
TOWER_URL_BASE      = 'http://awxweb'
LOGOUT_REDIRECT_URL = '/'
LOGIN_URL           = '/'
```

- **`TOWER_URL_BASE`** — URL utilisée dans les emails de notification AWX et les liens internes. En interne, AWX communique via `http://awxweb` (nom du conteneur). Pour les notifications envoyées aux utilisateurs, configurez-la vers l'URL publique dans `awx/settings.py` ou via le paramètre AWX **System → Miscellaneous System → Base URL of the Tower Host**.
- **`LOGOUT_REDIRECT_URL`** — Redirige vers la page de login personnalisée d'Autoflow après déconnexion.

---

## Execution Environments — Runtime

```python
CONTAINER_RUNTIME = 'docker'

DEFAULT_CONTAINER_RUN_OPTIONS = [
    '--network', 'bridge',
    '--add-host', f"git.{DOMAIN}:host-gateway",
    '--env', 'GIT_SSL_CAINFO=/etc/autoflow/ca.crt',
    '--env', 'SSL_CERT_FILE=/etc/autoflow/ca.crt',
    # + '--dns', EE_DNS_SERVER  si EE_DNS_SERVER est défini
]
```

### `CONTAINER_RUNTIME=docker`

AWX 24+ utilise Podman par défaut (rootless). Dans Autoflow, le socket Docker est bind-monté dans `awx_task`. Sans ce paramètre, AWX tente d'appeler `podman` qui n'existe pas et les jobs échouent immédiatement.

### `DEFAULT_CONTAINER_RUN_OPTIONS`

Ces options sont passées à chaque `docker run` qui lance un Execution Environment :

| Option | Rôle |
|---|---|
| `--network bridge` | Remplace `slirp4netns` (Podman) incompatible avec Docker |
| `--add-host git.DOMAIN:host-gateway` | Permet aux EE de résoudre `git.DOMAIN` vers le bridge Docker (Traefik) |
| `--env GIT_SSL_CAINFO` | Trust de la CA PKI pour les opérations git |
| `--env SSL_CERT_FILE` | Trust de la CA PKI pour Python/curl |
| `--dns EE_DNS_SERVER` | DNS explicite (optionnel, déconseillé — voir ci-dessous) |

### `EE_DNS_SERVER` — À utiliser avec précaution

```bash
# .env — laisser VIDE par défaut
EE_DNS_SERVER=
```

!!! danger "Risque DNS si mal configuré"
    Si `EE_DNS_SERVER` pointe vers un serveur inaccessible depuis les conteneurs Docker (ex: `127.0.0.53` de systemd-resolved), **toute résolution DNS échoue** dans les EE sans fallback. Docker génère automatiquement un `resolv.conf` correct — ne définissez `EE_DNS_SERVER` que si vous avez un serveur DNS dédié fiable et accessible depuis le bridge Docker.

---

## Galaxy / Collections (air-gapped)

```python
_galaxy_task_env_raw = os.environ.get('GALAXY_TASK_ENV_JSON', '')
if _galaxy_task_env_raw:
    import json as _json
    GALAXY_TASK_ENV = _json.loads(_galaxy_task_env_raw)
```

Pour bloquer tous les appels à `galaxy.ansible.com` (déploiement sans internet) :

```bash
# .env
GALAXY_TASK_ENV_JSON={"ANSIBLE_GALAXY_SERVER_LIST": ""}
```

!!! tip "Stratégie recommandée"
    Pré-installez toutes les collections nécessaires dans l'image EE lors du build (`make ee-build`). AWX détecte que les collections sont déjà présentes et saute le téléchargement — pas besoin de bloquer Galaxy si les EE sont correctement construits.

---

## AWX Isolation — Chemins montés dans les EE

```python
AWX_ISOLATION_BASE_PATH = '/tmp'

AWX_ISOLATION_SHOW_PATHS = [
    "/path/to/traefik/certs/ca.DOMAIN.crt:/etc/autoflow/ca.crt:ro",
]
```

- **`AWX_ISOLATION_BASE_PATH`** — Répertoire de travail pour les environnements isolés par job. Chaque job reçoit un répertoire privé sous `/tmp/ansible-runner-XXXXXX`.
- **`AWX_ISOLATION_SHOW_PATHS`** — Chemins de l'**hôte Docker** bind-montés dans chaque EE. Utilisé pour injecter le certificat CA PKI.

!!! warning "Chemins hôte uniquement"
    `AWX_ISOLATION_SHOW_PATHS` doit référencer des chemins existants sur **l'hôte Docker**, pas à l'intérieur d'un conteneur. Les volumes Docker nommés ne peuvent pas être listés ici.

---

## Premier démarrage — `awx_migrate`

Au premier démarrage, le conteneur `awx_migrate` exécute les migrations Django et crée l'utilisateur admin. Ce processus prend **2 à 5 minutes**.

```bash
# Surveiller les migrations
make logs SERVICES=awx_migrate

# Logs attendus
[...] Running database migrations...
[...] Creating admin user...
[...] AWX initialization complete.
```

`awx_web` et `awx_task` attendent que `awx_migrate` se termine avec un code de sortie 0 avant de démarrer (condition `service_completed_successfully` dans `docker-compose.yml`).

!!! info "Si awx_migrate dépasse 10 minutes"
    Vérifiez l'état de PostgreSQL (`make logs SERVICES=postgres`) et l'espace disque disponible. Un disque plein ou PostgreSQL non démarré sont les causes les plus fréquentes.

---

## Tokens AWX

Les tokens AWX sont utilisés par l'Event Engine et d'autres services pour déclencher des jobs via l'API AWX REST.

**Création dans l'interface AWX :**

1. Connexion → menu utilisateur (haut droite) → **Tokens**
2. Cliquer **Add**
3. Description : ex. `autoflow-event-engine`
4. **Scope** : `Write` (nécessaire pour lancer des jobs)
5. Copier le token généré (affiché une seule fois)
6. Mettre à jour `AWX_TOKEN` dans `.env`

```bash
# Vérification du token via API
curl -sk -H "Authorization: Bearer YOUR_TOKEN" \
  https://awx.DOMAIN/api/v2/me/ | python3 -m json.tool
```

---

## Job Templates

Un Job Template AWX définit :
- L'**Execution Environment** utilisé
- Le **playbook** à exécuter
- L'**inventaire** cible
- Les **credentials** (SSH, Vault, etc.)
- Les **extra_vars** par défaut

L'Event Engine référence les templates par leur **ID numérique** (`job_template_id` dans `rules.yml`).

```bash
# Lister tous les job templates via API
curl -sk -H "Authorization: Bearer ${AWX_TOKEN}" \
  https://awx.DOMAIN/api/v2/job_templates/ \
  | python3 -m json.tool | grep -E '"id"|"name"'
```

---

## Image AWX Patchée

Autoflow utilise une image AWX personnalisée (`autoflow/awx-patched`) qui intègre :
- Page de login personnalisée (`awx/custom/`)
- Logo et CSS Autoflow
- Corrections de compatibilité Docker Compose

```bash
# Reconstruire l'image patchée
make awx-build

# Pousser vers le registre Gitea
make awx-push

# Reconstruire + pousser
make awx-build awx-push
```

Le `Dockerfile.patched` se trouve dans `awx/Dockerfile.patched`.

---

## Appliquer les changements de configuration

Après modification de `awx/settings.py` ou des variables d'environnement AWX :

```bash
# Redémarrer awx_web et awx_task (awx_migrate n'est pas nécessaire)
docker compose restart awx_web awx_task

# Vérifier que les services redémarrent correctement
make status
make logs SERVICES=awx_web
```

!!! info "Pas de reload à chaud"
    AWX ne supporte pas le reload de configuration sans redémarrage. Les jobs en cours d'exécution au moment du redémarrage seront marqués comme `failed`. Planifiez les changements en dehors des fenêtres de maintenance actives.

---

## Référence rapide — Commandes de diagnostic

```bash
# Vérifier la config chargée (depuis awx_web)
docker exec autoflow_awx_web awx-manage diffsettings

# Vérifier la connectivité AWX → PostgreSQL
docker exec autoflow_awx_web awx-manage check --database

# Réinitialiser le mot de passe admin oublié
docker exec -it autoflow_awx_web awx-manage changepassword admin

# Voir les migrations appliquées
docker exec autoflow_awx_migrate awx-manage showmigrations | tail -20

# Tester la connexion Redis
docker exec autoflow_redis redis-cli -a "${REDIS_PASSWORD}" ping
```
