---
title: Sécurité réseau
---

# Sécurité réseau

---

## Isolation réseau Docker

Tous les services Autoflow partagent le réseau interne `autoflow_net` (bridge Docker). Aucun port interne n'est exposé sur `0.0.0.0` — tout passe par Traefik.

```bash
# Vérifier les ports exposés sur l'hôte (doivent être 80, 443, 2222 uniquement)
docker ps --format "{{.Names}}: {{.Ports}}" | grep -v "^$"
```

---

## Firewall recommandé

=== "UFW (Ubuntu)"

    ```bash
    # Bloquer tout par défaut
    sudo ufw default deny incoming
    sudo ufw default allow outgoing

    # Autoriser SSH admin
    sudo ufw allow 22/tcp

    # Autoriser le trafic web
    sudo ufw allow 80/tcp
    sudo ufw allow 443/tcp
    sudo ufw allow 2222/tcp   # Git SSH (si utilisé)

    # Activer
    sudo ufw enable
    sudo ufw status verbose
    ```

---

## Headers de sécurité HTTP

Traefik injecte les headers de sécurité via les middlewares dans `traefik/dynamic/` :

```yaml
# traefik/dynamic/headers.yml
http:
  middlewares:
    secure-headers:
      headers:
        stsSeconds: 31536000
        stsIncludeSubdomains: true
        contentTypeNosniff: true
        browserXssFilter: true
        referrerPolicy: "strict-origin-when-cross-origin"
        customFrameOptionsValue: "SAMEORIGIN"
```

---

## CORS

La liste des origines autorisées est définie par `CORS_ORIGINS` (dérivée automatiquement depuis `DOMAIN`) :

```bash
CORS_ORIGINS=https://awx.client.example.com,https://api.client.example.com,https://pki.client.example.com
```

En production, ne jamais mettre `*`.

---

## ALLOWED_HOSTS AWX

Django AWX vérifie le header `Host` de chaque requête. La valeur est configurée via `AWX_ALLOWED_HOSTS` :

```bash
AWX_ALLOWED_HOSTS=awx.client.example.com,localhost,awxweb
```

Toujours inclure :
- Le sous-domaine public (`awx.<DOMAIN>`)
- `localhost` (health checks internes)
- `awxweb` (nom Docker interne, utilisé par `TOWER_URL_BASE`)

---

## Rate limiting API

```bash
RATE_LIMIT=100/minute   # Par IP, format slowapi
```

Formats valides : `second`, `minute`, `hour`, `day`.

---

## Protection des endpoints sensibles

| Endpoint | Protection |
|---|---|
| Deploy Wizard | HTTP Basic Auth (WIZARD_TOKEN) |
| Prometheus | BasicAuth Traefik (MONITORING_ADMIN) |
| Alertmanager | BasicAuth Traefik (MONITORING_ADMIN) |
| PKI | JWT (PKI_JWT_SECRET) |
| Event Engine /admin/* | Bearer Token (EVENT_ENGINE_ADMIN_TOKEN) |
| Compliance reports | Bearer Token (COMPLIANCE_ADMIN_TOKEN) |
