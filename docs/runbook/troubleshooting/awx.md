---
title: Dépannage AWX
---

# Dépannage AWX

---

## Problème 1 — AWX répond HTTP 502

**Symptôme** : Traefik renvoie un 502 Bad Gateway sur `https://awx.<DOMAIN>`.

**Diagnostic** :
```bash
# AWX web est-il démarré ?
docker compose ps awx_web
docker logs autoflow_awx_web --tail=30

# Vérifier le port interne
docker exec autoflow_awx_web curl -s http://localhost:8052/health/ | head -5
```

**Causes possibles et solutions** :

=== "Container en cours de démarrage"

    AWX met 1-3 minutes à démarrer. Attendre et recharger.

    ```bash
    # Surveiller le démarrage
    watch -n 5 'docker compose ps awx_web'
    ```

=== "Erreur de configuration settings.py"

    ```bash
    docker logs autoflow_awx_web --tail=50 | grep -i "error\|exception\|improperly"
    
    # Corriger settings.py et redémarrer
    docker compose restart awx_web awx_task
    ```

=== "PostgreSQL inaccessible"

    ```bash
    docker compose exec awx_postgres pg_isready -U awx
    docker compose restart awx_postgres
    sleep 15
    docker compose restart awx_web awx_task
    ```

=== "Problème de réseau Docker"

    ```bash
    docker network inspect autoflow_net | grep -A5 "awx_web"
    # Si absent, recréer
    docker compose up -d awx_web
    ```

---

## Problème 2 — Jobs bloqués en "pending"

**Symptôme** : Les jobs restent en statut "pending" indéfiniment.

**Diagnostic** :
```bash
# Voir les jobs pending
curl -u admin:<PASS> "https://awx.<DOMAIN>/api/v2/jobs/?status=pending" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Pending jobs:', d['count'])"

# Vérifier awx_task
docker logs autoflow_awx_task --tail=50 | grep -i "error\|dispatch\|instance"
```

**Solutions** :

```bash
# 1. Vérifier l'instance group
curl -u admin:<PASS> "https://awx.<DOMAIN>/api/v2/instances/"

# 2. Redémarrer awx_task
docker compose restart awx_task
sleep 30

# 3. Si toujours bloqué, annuler les jobs pending
curl -u admin:<PASS> "https://awx.<DOMAIN>/api/v2/jobs/?status=pending" \
  | python3 -c "import sys,json; [print(j['id']) for j in json.load(sys.stdin)['results']]" \
  | while read id; do
    curl -X POST -u admin:<PASS> "https://awx.<DOMAIN>/api/v2/jobs/$id/cancel/"
  done
```

---

## Problème 3 — Erreur "ALLOWED_HOSTS" dans les logs

**Symptôme** : Logs AWX contiennent `DisallowedHost: Invalid HTTP_HOST header`.

**Diagnostic** :
```bash
docker logs autoflow_awx_web 2>&1 | grep "DisallowedHost"
```

**Solution** :
```bash
# Vérifier la valeur actuelle
grep AWX_ALLOWED_HOSTS .env

# Corriger (doit inclure le domaine public, localhost, awxweb)
# .env :
# AWX_ALLOWED_HOSTS=awx.example.com,localhost,awxweb

# Redémarrer
docker compose restart awx_web awx_task
```

---

## Problème 4 — Login AWX échoue ("Invalid username or password")

**Solutions** :

```bash
# Reset du mot de passe admin via la CLI Django
docker compose exec awx_task awx-manage changepassword admin
# Saisir le nouveau mot de passe quand demandé

# Ou via API (si vous avez encore un token valide)
curl -X PATCH -u admin:<ANCIEN_PASS> \
  "https://awx.<DOMAIN>/api/v2/me/" \
  -H "Content-Type: application/json" \
  -d '{"password": "<NOUVEAU_PASS>"}'
```

---

## Problème 5 — Execution Environment introuvable

**Symptôme** : Job échoue avec "Could not find Execution Environment image".

**Diagnostic** :
```bash
# Lister les EE dans AWX
curl -u admin:<PASS> "https://awx.<DOMAIN>/api/v2/execution_environments/" \
  | python3 -c "import sys,json; [print(e['name'], e['image']) for e in json.load(sys.stdin)['results']]"

# Vérifier que l'image est accessible depuis AWX
docker compose exec awx_task docker pull git.<DOMAIN>/<ORG>/<IMAGE>:latest
```

**Solutions** :

=== "Image non existante dans le registry"

    ```bash
    # Pousser l'image dans Gitea Container Registry
    docker tag <image>:<tag> git.<DOMAIN>/<ORG>/<IMAGE>:latest
    docker push git.<DOMAIN>/<ORG>/<IMAGE>:latest
    ```

=== "Credentials registry manquants dans AWX"

    Dans AWX UI : **Credentials → + Add → Container Registry**
    - Server: `git.<DOMAIN>`
    - Username: compte Gitea
    - Password: `GITEA_REGISTRY_TOKEN`

=== "Certificat TLS non reconnu par AWX"

    ```bash
    # Copier le certificat CA dans AWX
    docker compose exec awx_task \
      bash -c "cp /etc/ssl/certs/ca-certificates.crt /etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem"
    
    # Ou ajouter au compose : volumes pour monter le certificat CA
    ```

---

## Problème 6 — AWX ne peut pas cloner le projet Git

**Symptôme** : Job échoue avec "ERROR! Unexpected Exception, this is probably a bug: Failed to update project".

**Diagnostic** :
```bash
# Voir les logs de mise à jour du projet
curl -u admin:<PASS> "https://awx.<DOMAIN>/api/v2/projects/<ID>/project_updates/?page_size=5" \
  | python3 -m json.tool | grep -A5 '"result_stdout"'
```

**Solutions** :

=== "Erreur SSL/certificat"

    ```bash
    # Dans le projet AWX, activer "Allow Branch Override" et tester
    # Ou désactiver la vérification SSL (dev uniquement) :
    # Settings → Jobs → Extra Environment Variables :
    # {"GIT_SSL_NO_VERIFY": "true"}
    ```

=== "Credentials Git incorrects"

    ```bash
    # Vérifier que le credential Git dans AWX est à jour
    # Tester manuellement avec le token
    git clone https://<token>:x-oauth-basic@git.<DOMAIN>/<ORG>/<REPO>.git /tmp/test-clone
    ```

=== "Gitea inaccessible depuis awx_task"

    ```bash
    docker compose exec awx_task curl -sk https://git.<DOMAIN>/api/v1/repos/search?limit=1
    # Si échec, voir Dépannage Réseau
    ```

---

## Problème 7 — Webhooks Gitea ne déclenchent pas de jobs

**Symptôme** : Un push sur Gitea n'entraîne aucun job AWX.

**Diagnostic** :
```bash
# Vérifier que l'Event Engine reçoit les webhooks
docker logs autoflow_event_engine --tail=20 | grep "webhook_received"

# Vérifier les logs Gitea pour les deliveries de webhook
# Gitea UI : Settings → Webhooks → (cliquer) → Recent Deliveries
```

**Solutions** :

```bash
# Tester l'Event Engine manuellement
curl -X POST https://api.<DOMAIN>/webhook/gitea \
  -H "X-Gitea-Event: push" \
  -H "Content-Type: application/json" \
  -d '{"repository": {"name": "test"}, "ref": "refs/heads/main"}'

# Vérifier les variables d'environnement Event Engine
docker compose exec event_engine env | grep AWX

# Redémarrer si le token a changé
docker compose up -d --force-recreate event_engine
```

---

## Problème 8 — Logs AWX manquants (rsyslog)

**Symptôme** : Activité invisible dans Activity Stream, pas de logs job.

**Diagnostic** :
```bash
docker logs autoflow_awx_rsyslog --tail=20
docker compose ps awx_rsyslog
```

**Solution** :
```bash
docker compose restart awx_rsyslog awx_web awx_task
```

---

## Problème 9 — Job échoue avec "No module named X"

**Symptôme** : Erreur Python dans les logs de job.

**Solution** :

Le module manque dans l'Execution Environment. Mettre à jour l'EE :

```bash
# Dans l'EE builder (execution-environment.yml)
# Ajouter dans python_interpreter_path dependencies :
# - python_requirements.txt avec le module manquant

# Rebuild l'EE
ansible-builder build -t git.<DOMAIN>/<ORG>/ee-custom:latest
docker push git.<DOMAIN>/<ORG>/ee-custom:latest

# Forcer AWX à re-pull l'image
# AWX UI : Execution Environments → Pull : Always
```

---

## Problème 10 — AWX trop lent / timeout

**Symptôme** : Interface AWX très lente, jobs qui timeout.

**Diagnostic** :
```bash
# Ressources AWX
docker stats --no-stream autoflow_awx_web autoflow_awx_task autoflow_awx_postgres

# Connexions PostgreSQL
docker compose exec awx_postgres psql -U awx -c \
  "SELECT count(*), state FROM pg_stat_activity GROUP BY state;"

# Jobs actifs en parallèle
curl -u admin:<PASS> "https://awx.<DOMAIN>/api/v2/jobs/?status=running" \
  | python3 -c "import sys,json; print('Running:', json.load(sys.stdin)['count'])"
```

**Solutions** :

```bash
# Augmenter la mémoire AWX (dans docker-compose.yml ou .env)
# Limiter le nombre de jobs parallèles
# AWX UI : Instance Groups → default → Max Concurrent Jobs → 5

# Vacuum PostgreSQL
docker compose exec awx_postgres psql -U awx -c "VACUUM ANALYZE;"
```

---

## Problème 11 — Mise à jour AWX échoue (migration DB)

```bash
# Voir l'état des migrations
docker compose exec awx_task awx-manage showmigrations | grep "\[ \]"

# Lancer les migrations manuellement
docker compose exec awx_task awx-manage migrate --run-syncdb

# Si bloqué, vérifier les locks PostgreSQL
docker compose exec awx_postgres psql -U awx -c \
  "SELECT pid, query, state FROM pg_stat_activity WHERE wait_event_type='Lock';"

# Tuer les locks si nécessaire (avec précaution)
docker compose exec awx_postgres psql -U awx -c "SELECT pg_terminate_backend(<PID>);"
```

---

## Problème 12 — Secret Key AWX changée

**Symptôme** : "Encryption/decryption failed" dans les logs ; credentials AWX illisibles.

!!! danger "Critique"
    Si `SECRET_KEY` change, **toutes les données chiffrées en base sont irrécupérables** sans les anciennes clés.

```bash
# Vérifier la clé actuelle
docker compose exec awx_web env | grep SECRET_KEY

# Ne JAMAIS changer SECRET_KEY sur une instance existante
# Si changée par erreur : restaurer depuis backup
```

---

## Problème 13 — Container awx_web redémarre en boucle

```bash
# Logs de démarrage
docker logs autoflow_awx_web --tail=100 2>&1 | head -50

# Causes fréquentes :
# - PostgreSQL pas encore prêt → attendre + redémarrer
# - settings.py syntaxiquement invalide → corriger
# - Variable d'environnement manquante → vérifier .env

# Forcer la recréation
docker compose up -d --force-recreate awx_web
```

---

## Problème 14 — Tâches planifiées (Schedules) ne s'exécutent pas

```bash
# Vérifier les schedules AWX
curl -u admin:<PASS> "https://awx.<DOMAIN>/api/v2/schedules/?enabled=true" \
  | python3 -c "import sys,json; [print(s['name'], s['next_run']) for s in json.load(sys.stdin)['results']]"

# Vérifier la timezone AWX (doit être UTC ou la bonne TZ)
docker compose exec awx_web env | grep TZ

# Forcer l'évaluation des schedules
docker compose exec awx_task awx-manage run_dispatcher --status
```

---

## Problème 15 — Importation de playbook impossible (roles introuvables)

```bash
# Vérifier les requirements.yml du projet
# Dans le projet Git : ls roles/requirements.yml collections/requirements.yml

# Forcer la mise à jour des requirements
curl -X POST -u admin:<PASS> \
  "https://awx.<DOMAIN>/api/v2/projects/<ID>/update/" \
  -H "Content-Type: application/json"

# Voir les logs de mise à jour
curl -u admin:<PASS> "https://awx.<DOMAIN>/api/v2/project_updates/?order_by=-id&page_size=1" \
  | python3 -m json.tool | grep "result_stdout" -A 20
```
