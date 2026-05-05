---
title: Security Scanner
---

# Security Scanner

Le service `security_scanner` intègre **Trivy** pour scanner les images Docker de la stack Autoflow à intervalles réguliers et générer des rapports de conformité aux frameworks de sécurité.

---

## Fonctionnalités

| Fonctionnalité | Description |
|---|---|
| **Scan CVE** | Détecte les vulnérabilités connues (CVE) dans toutes les images Docker de la stack |
| **Version check** | Vérifie si des versions plus récentes sont disponibles pour les images |
| **Rapports de conformité** | Mappe les CVEs aux contrôles NIST SP 800-53, CIS Controls v8, ISO 27001:2022, PCI-DSS v4.0, SOC 2 TSC |
| **API REST** | Endpoints pour déclencher des scans et télécharger les rapports |

---

## Configuration

| Variable | Défaut | Description |
|---|---|---|
| `SECURITY_SCANNER_PORT` | `8002` | Port interne |
| `SCAN_INTERVAL` | `21600` | Intervalle scan CVE en secondes (21600 = 6h) |
| `VERSION_CHECK_INTERVAL` | `3600` | Intervalle vérif versions (3600 = 1h) |
| `TRIVY_VERSION` | `0.63.0` | Version Trivy (pinned) |
| `TRIVY_TIMEOUT` | `300` | Timeout par image en secondes |
| `COMPLIANCE_ADMIN_TOKEN` | — | Token Bearer pour les endpoints compliance |
| `GITHUB_TOKEN` | — | Token GitHub (5000 req/h vs 60 req/h sans) |
| `DOCKERHUB_USER` / `DOCKERHUB_PASSWORD` | — | Credentials DockerHub pour les rate limits |

---

## Rapports de conformité

Les rapports mappent les CVEs trouvées aux contrôles de sécurité des frameworks :

| Framework | Scope |
|---|---|
| NIST SP 800-53 | Contrôles fédéraux US |
| CIS Controls v8 | Bonnes pratiques industrie |
| ISO 27001:2022 | Standard international |
| PCI-DSS v4.0 | Secteur bancaire/paiement |
| SOC 2 TSC | Trust Service Criteria |

### Générer un rapport

```bash
# Via l'API (Bearer token requis)
curl -X POST https://api.<DOMAIN>/compliance/report/generate \
  -H "Authorization: Bearer $COMPLIANCE_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"framework": "nist", "format": "pdf"}'

# Télécharger le dernier rapport
curl https://api.<DOMAIN>/compliance/report/latest \
  -H "Authorization: Bearer $COMPLIANCE_ADMIN_TOKEN" \
  -o compliance-report.pdf
```

---

## Accès au scanner

Le scanner est accessible en interne. Les endpoints sont protégés par `COMPLIANCE_ADMIN_TOKEN` :

```bash
# Santé
curl http://localhost:8002/health

# Derniers résultats de scan
curl -H "Authorization: Bearer $COMPLIANCE_ADMIN_TOKEN" \
  http://localhost:8002/scan/results
```

---

## GitHub Token (recommandé)

Sans token GitHub, l'API publique est limitée à **60 requêtes/heure** — insuffisant pour les vérifications de version fréquentes.

Avec un token (Personal Access Token, read-only) : **5000 requêtes/heure**.

```bash
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```
