---
title: Durcissement TLS
---

# Durcissement TLS

---

## Configuration Traefik TLS

Traefik applique TLS 1.2 minimum par défaut. Pour forcer TLS 1.3 uniquement :

```yaml
# traefik/dynamic/tls.yml
tls:
  options:
    default:
      minVersion: VersionTLS13
      cipherSuites:
        - TLS_AES_256_GCM_SHA384
        - TLS_CHACHA20_POLY1305_SHA256
        - TLS_AES_128_GCM_SHA256
```

---

## HSTS

HSTS (HTTP Strict Transport Security) force les navigateurs à utiliser HTTPS :

```yaml
# traefik/dynamic/headers.yml
http:
  middlewares:
    hsts:
      headers:
        stsSeconds: 31536000        # 1 an
        stsIncludeSubdomains: true
        stsPreload: true
```

---

## Vérifier la configuration TLS

```bash
# Tester depuis l'extérieur
openssl s_client -connect awx.<DOMAIN>:443 -servername awx.<DOMAIN> </dev/null 2>&1 \
  | grep -E "Protocol|Cipher|Verify"

# Ou via sslyze (si installé)
sslyze awx.<DOMAIN>:443
```

---

## Certificats — bonnes pratiques

- **Durée de validité** : 1 an maximum (PKI interne configurée à 1 an)
- **Taille de clé** : RSA 2048 minimum, ECDSA P-256 recommandé
- **Alerte expiration** : configurée à 30 jours (règle Prometheus)
- **Révocation** : via l'interface PKI `https://pki.<DOMAIN>`
