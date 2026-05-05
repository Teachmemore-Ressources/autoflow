---
title: Post-installation
---

# Post-installation

Checklist des actions à effectuer **après** le premier `make start` réussi.

---

## Checklist complète

- [ ] Accès aux interfaces web vérifié
- [ ] Certificat CA installé dans les navigateurs
- [ ] Runner Gitea Actions enregistré
- [ ] Playbooks réseau initialisés dans Gitea
- [ ] Premier EE buildé et pushé
- [ ] AWX configuré (credentials, projet, inventaire)
- [ ] Backup configuré et testé
- [ ] Alertes monitoring configurées
- [ ] `.env` chiffré et commité

---

## 1. Vérifier l'accès aux interfaces

Ouvrez chaque URL dans votre navigateur (remplacer `<DOMAIN>` par votre domaine) :

| Interface | URL | Credentials |
|---|---|---|
| **AWX** | `https://awx.<DOMAIN>` | `AWX_ADMIN_USER` / `AWX_ADMIN_PASSWORD` |
| **Gitea** | `https://git.<DOMAIN>` | `GITEA_ADMIN_USER` / `GITEA_ADMIN_PASSWORD` |
| **Grafana** | `https://grafana.<DOMAIN>` | `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD` |
| **PKI** | `https://pki.<DOMAIN>` | `PKI_ADMIN_USER` / `PKI_ADMIN_PASSWORD` |
| **Prometheus** | `https://prometheus.<DOMAIN>` | `MONITORING_ADMIN_USER` / `MONITORING_ADMIN_PASSWORD` |

!!! warning "Avertissement certificat"
    À la première connexion, votre navigateur peut afficher un avertissement "certificat non reconnu". C'est normal — le CA interne d'Autoflow n'est pas encore dans votre magasin de confiance. Voir l'étape 2.

---

## 2. Installer le certificat CA

Deux méthodes pour faire confiance au CA Autoflow :

=== "Téléchargement navigateur"

    1. Naviguer sur `https://pki.<DOMAIN>/ca/download` (ignorez l'avertissement une seule fois)
    2. Télécharger le fichier `ca.<DOMAIN>.crt`
    3. Installer dans votre navigateur / OS :

    === "Chrome / Edge"
        `Paramètres → Confidentialité → Certificats → Autorités de certification → Importer`

    === "Firefox"
        `Paramètres → Vie privée → Afficher les certificats → Autorités → Importer`

    === "macOS"
        ```bash
        sudo security add-trusted-cert -d -r trustRoot \
          -k /Library/Keychains/System.keychain ca.<DOMAIN>.crt
        ```

    === "Ubuntu / Debian"
        ```bash
        sudo cp ca.<DOMAIN>.crt /usr/local/share/ca-certificates/autoflow-ca.crt
        sudo update-ca-certificates
        ```

=== "Via make (serveur)"

    ```bash
    # Installe le CA dans le trust store Docker et système du serveur
    make docker-trust-ca
    ```

    Nécessaire pour que Docker puisse pull depuis le registry Gitea avec HTTPS.

---

## 3. Initialiser Gitea Actions Runner

```bash
make gitea-init-runner
```

Résultat attendu :
```
✔ Gitea disponible.
✔ Token runner obtenu.
✔ GITEA_RUNNER_TOKEN écrit dans .env
✔ act_runner recréé avec le nouveau token.
✔ Runner 'autoflow-runner' enregistré dans Gitea
```

Vérification dans Gitea : **Site Administration → Actions → Runners**

---

## 4. Initialiser les playbooks réseau

```bash
make gitea-init-network
```

Ce script crée (ou met à jour) le dépôt `network-playbooks` dans Gitea avec les playbooks Ansible de base.

---

## 5. Builder et pousser les Execution Environments

Les EE sont les images Docker qu'AWX utilise pour exécuter les playbooks. Il en existe trois dans Autoflow :

```bash
# EE de base (collections génériques)
make ee-build-push EE=base VERSION=1.0.0

# EE réseau (modules Cisco, Arista, NAPALM...)
make ee-build-push EE=network VERSION=1.0.0

# EE sécurité (modules audit, compliance...)
make ee-build-push EE=security VERSION=1.0.0
```

!!! info "Prérequis"
    ansible-builder doit être installé :
    ```bash
    make ee-deps
    ```
    Et le CA Docker doit être trusté :
    ```bash
    make docker-trust-ca
    ```

Vérifier dans Gitea que les images sont disponibles : **git.<DOMAIN> → Packages**

---

## 6. Configurer AWX

### Créer un credential Git

Dans AWX :
1. **Credentials → Add**
2. Type : `Source Control`
3. URL : `https://git.<DOMAIN>/<GITEA_USER>/network-playbooks`
4. Username : `GITEA_ADMIN_USER`
5. Password : `GITEA_ADMIN_PASSWORD` (ou un token Gitea dédié)

### Créer un projet AWX

1. **Projects → Add**
2. Name : `Network Playbooks`
3. SCM Type : `Git`
4. SCM URL : `https://git.<DOMAIN>/<GITEA_USER>/network-playbooks.git`
5. Credential : celui créé ci-dessus
6. **Save** → le projet se synchronise automatiquement

### Créer un inventaire

1. **Inventories → Add**
2. Name : `Production`
3. Ajouter vos hôtes (ou sourcer depuis un SCM)

### Créer un Job Template

1. **Templates → Add → Job Template**
2. Name : `Deploy Network Config`
3. Inventory : `Production`
4. Project : `Network Playbooks`
5. Playbook : sélectionner votre playbook
6. Execution Environment : sélectionner `ee-base:1.0.0`

### Récupérer le token AWX pour l'Event Engine

1. AWX → **User menu (haut droite) → Tokens → Add**
2. Description : `Event Engine`
3. Scope : `Write`
4. Copier le token généré
5. Le coller dans le wizard : **Section AWX → API Token** → Sauvegarder

---

## 7. Configurer le backup

Configurez la cible de backup dans le wizard (Section **Backup & DR**) :

=== "Local (lab uniquement)"
    ```
    BACKUP_BACKEND=local
    BACKUP_LOCAL_PATH=/var/backups/autoflow/restic
    ```
    Créer le répertoire :
    ```bash
    sudo mkdir -p /var/backups/autoflow/restic
    sudo chown $USER:$USER /var/backups/autoflow/restic
    ```

=== "SFTP (recommandé en production)"
    ```
    BACKUP_BACKEND=sftp
    BACKUP_LOCAL_PATH=sftp://user@backup-server:22/backups/autoflow
    ```

=== "S3 / Compatible"
    ```
    BACKUP_BACKEND=s3
    BACKUP_S3_BUCKET=autoflow-backup
    BACKUP_S3_ENDPOINT=https://s3.example.com   # vide = AWS S3
    BACKUP_S3_ACCESS_KEY=<access-key>
    BACKUP_S3_SECRET_KEY=<secret-key>
    ```

Tester le backup :
```bash
make backup
```

---

## 8. Configurer les alertes monitoring

Voir le fichier `monitoring/alertmanager/alertmanager.yml` pour configurer les destinations d'alertes (Slack, email, webhook).

```yaml
# Exemple Slack
receivers:
  - name: 'slack'
    slack_configs:
      - api_url: 'https://hooks.slack.com/services/...'
        channel: '#autoflow-alerts'
        send_resolved: true
```

Redémarrer Alertmanager après modification :
```bash
docker compose restart alertmanager
```

---

## 9. Chiffrer et versionner les secrets

```bash
# Chiffrer .env → .env.enc
make secrets-encrypt

# Vérifier que .env est dans .gitignore
grep ".env$" .gitignore

# Commiter le fichier chiffré
git add .env.enc
git commit -m "chore: initial encrypted configuration"
git push
```

---

## 10. Configurer les webhooks Gitea → Event Engine

Dans Gitea, pour chaque dépôt qui doit déclencher des jobs AWX :

1. **Repository → Settings → Webhooks → Add**
2. Target URL : `https://api.<DOMAIN>/events/webhook`
3. Content type : `application/json`
4. Secret : valeur de `GITEA_WEBHOOK_SECRET` dans `.env`
5. Events : cocher `Push`, `Create`, `Release` selon vos besoins

---

## Prochaines étapes

- 📊 [Dashboards Grafana](../operations/monitoring-dashboards.md) — explorer les dashboards préconfigurés
- 🔔 [Event Engine](../configuration/event-engine.md) — configurer les règles de routage webhook → AWX
- 🛡️ [Gestion des secrets](../security/secrets-management.md) — rotation des secrets, multi-membres
- 💾 [Backup & Restore](../operations/backup-restore.md) — tester et automatiser les backups
