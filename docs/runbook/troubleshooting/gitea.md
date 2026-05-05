---
title: Dépannage Gitea
---

# Dépannage Gitea

---

## Problème 1 — Gitea inaccessible (HTTP 502 / 503)

**Diagnostic** :
```bash
docker compose ps gitea
docker logs autoflow_gitea --tail=30

# Test interne
docker exec autoflow_gitea curl -s http://localhost:3000/api/v1/repos/search?limit=1 | head -5
```

**Solutions** :

=== "Container en cours de démarrage"

    Gitea peut prendre 30-60 secondes. Attendre et surveiller :
    ```bash
    watch -n 3 'docker compose ps gitea'
    ```

=== "Base de données SQLite corrompue"

    ```bash
    # Vérifier l'intégrité
    docker compose exec gitea sqlite3 /data/gitea/gitea.db "PRAGMA integrity_check;"
    
    # Si corrompu, restaurer depuis backup
    # Voir docs/operations/backup-restore.md
    ```

=== "Volume plein"

    ```bash
    df -h /var/lib/docker/volumes/autoflow_gitea_data/
    # Si plein : nettoyer les releases/artifacts inutilisés via Gitea UI
    ```

---

## Problème 2 — Push Git refusé ("remote: repository not found" ou permission denied)

**Diagnostic** :
```bash
# Tester l'accès avec le token
curl -sk "https://git.<DOMAIN>/api/v1/user" \
  -H "Authorization: token <GITEA_TOKEN>"

# Vérifier les permissions du repo
curl -sk "https://git.<DOMAIN>/api/v1/repos/<OWNER>/<REPO>" \
  -H "Authorization: token <GITEA_TOKEN>"
```

**Solutions** :

=== "Token expiré"

    Gitea UI : **Settings → Applications → Regenerate Token**
    Puis mettre à jour `.env` et redémarrer les services concernés.

=== "Droits insuffisants"

    Gitea UI : **Repo → Settings → Collaborators** — vérifier le niveau d'accès.

=== "Repo privé + pas de credentials dans git config"

    ```bash
    git remote set-url origin https://<TOKEN>:x-oauth-basic@git.<DOMAIN>/<OWNER>/<REPO>.git
    ```

---

## Problème 3 — Container Registry inaccessible

**Symptôme** : `docker pull git.<DOMAIN>/<org>/<image>:tag` échoue.

**Diagnostic** :
```bash
# Vérifier que le registry est activé (app.ini)
docker compose exec gitea cat /data/gitea/conf/app.ini | grep -A5 "\[packages\]"

# Test d'authentification
docker login git.<DOMAIN> -u <user> -p <GITEA_REGISTRY_TOKEN>
```

**Solutions** :

```bash
# Si le registry n'est pas activé dans app.ini :
# [packages]
# ENABLED = true
# Redémarrer Gitea

# Vérifier que l'image existe
curl -sk "https://git.<DOMAIN>/api/v1/packages/<OWNER>/container/<IMAGE>/tags/list" \
  -H "Authorization: token <TOKEN>"
```

---

## Problème 4 — Webhooks non délivrés

**Symptôme** : Un push ne déclenche pas le webhook.

**Diagnostic** :
```bash
# Dans Gitea UI : Repo → Settings → Webhooks → cliquer sur le webhook
# Onglet "Recent Deliveries" → voir le statut et la réponse

# Tester manuellement la délivrance
# Bouton "Test Delivery" dans l'interface
```

**Solutions** :

=== "Event Engine inaccessible depuis Gitea"

    ```bash
    # Gitea est dans le réseau Docker — tester l'URL interne
    docker compose exec gitea wget -q -O- http://event_engine:8000/health
    
    # Si inaccessible, vérifier le réseau autoflow_net
    docker network inspect autoflow_net | grep "event_engine"
    ```

=== "HTTPS avec certificat interne non reconnu"

    ```bash
    # Ajouter le certificat CA à Gitea
    # Copier le CA cert dans le container Gitea
    docker cp /path/to/ca.crt autoflow_gitea:/etc/ssl/certs/autoflow-ca.crt
    
    # Ou utiliser l'URL interne HTTP (dans le réseau Docker)
    # http://event_engine:8000/webhook/gitea
    ```

=== "Secret HMAC incorrect"

    ```bash
    # Vérifier le secret dans le webhook Gitea
    # Doit correspondre à GITEA_WEBHOOK_SECRET dans .env
    grep GITEA_WEBHOOK_SECRET .env
    ```

---

## Problème 5 — Actions Runner déconnecté

**Symptôme** : Les workflows Gitea Actions ne se lancent pas, runner apparaît offline.

**Diagnostic** :
```bash
docker compose ps gitea_runner
docker logs autoflow_gitea_runner --tail=30 | grep -i "error\|connect\|register"
```

**Solutions** :

```bash
# Recréer le runner (re-enregistrement automatique)
docker compose up -d --force-recreate gitea_runner

# Vérifier le token de registration
# Gitea UI : Site Administration → Runners → Create Registration Token
# Mettre à jour GITEA_RUNNER_REGISTRATION_TOKEN dans .env

# Si le runner s'enregistre mais n'accepte pas de jobs :
docker logs autoflow_gitea_runner --tail=50 | grep "job\|label\|accept"
```

---

## Problème 6 — Gitea lent ou timeouts

**Diagnostic** :
```bash
docker stats --no-stream autoflow_gitea

# Taille de la base de données
docker compose exec gitea ls -lh /data/gitea/gitea.db

# GC Git sur les repos volumineux
docker compose exec gitea gitea admin git-repositories run-task git-gc --all
```

---

## Problème 7 — Migration de dépôt échoue

```bash
# Logs de migration
docker logs autoflow_gitea 2>&1 | grep -i "migration\|error" | tail -20

# Lancer les migrations manuellement
docker compose exec gitea gitea migrate

# Vérifier la version (avant mise à jour, vérifier la compatibilité)
docker compose exec gitea gitea --version
```

---

## Problème 8 — Email Gitea non envoyé

```bash
# Vérifier la config SMTP dans app.ini
docker compose exec gitea cat /data/gitea/conf/app.ini | grep -A10 "\[mailer\]"

# Tester l'envoi d'email
docker compose exec gitea gitea admin user generate-access-token --username admin --token-name test

# Logs SMTP
docker logs autoflow_gitea 2>&1 | grep -i "mail\|smtp\|error" | tail -10
```

---

## Problème 9 — Réinitialisation du mot de passe admin Gitea

```bash
# Réinitialiser via CLI
docker compose exec gitea gitea admin user change-password \
  --username admin \
  --password <NOUVEAU_MOT_DE_PASSE>

# Vérifier que l'utilisateur existe
docker compose exec gitea gitea admin user list
```

---

## Problème 10 — Sauvegarde manuelle Gitea

```bash
# Dump complet (inclut DB, repos, config, attachments)
docker compose exec gitea gitea dump -c /data/gitea/conf/app.ini \
  --type tar.gz \
  --file /tmp/gitea-backup-$(date +%Y%m%d).tar.gz

# Copier hors du container
docker cp autoflow_gitea:/tmp/gitea-backup-$(date +%Y%m%d).tar.gz ./
```
