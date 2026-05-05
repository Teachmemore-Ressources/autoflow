---
title: Conformité
---

# Conformité & Rapports

Le Security Scanner génère des rapports de conformité en mappant les CVEs trouvées aux contrôles des frameworks de sécurité.

---

## Frameworks supportés

| Framework | Version | Scope |
|---|---|---|
| NIST SP 800-53 | Rev 5 | Contrôles fédéraux US |
| CIS Controls | v8 | Bonnes pratiques industrie |
| ISO 27001 | 2022 | Standard international |
| PCI-DSS | v4.0 | Secteur paiement |
| SOC 2 | TSC 2017 | Trust Service Criteria |

---

## Générer un rapport

```bash
# NIST SP 800-53
curl -X POST https://api.<DOMAIN>/compliance/report/generate \
  -H "Authorization: Bearer $COMPLIANCE_ADMIN_TOKEN" \
  -d '{"framework": "nist-800-53"}'

# CIS Controls v8
curl -X POST https://api.<DOMAIN>/compliance/report/generate \
  -H "Authorization: Bearer $COMPLIANCE_ADMIN_TOKEN" \
  -d '{"framework": "cis-v8"}'

# ISO 27001
curl -X POST https://api.<DOMAIN>/compliance/report/generate \
  -H "Authorization: Bearer $COMPLIANCE_ADMIN_TOKEN" \
  -d '{"framework": "iso-27001"}'
```

---

## Télécharger le rapport

```bash
curl https://api.<DOMAIN>/compliance/report/latest \
  -H "Authorization: Bearer $COMPLIANCE_ADMIN_TOKEN" \
  -o compliance-$(date +%Y%m%d).pdf
```

---

## Interpréter les résultats

Le rapport contient pour chaque CVE :
- **CVE ID** et sévérité (CRITICAL/HIGH/MEDIUM/LOW)
- **Image Docker** affectée
- **Contrôle du framework** correspondant
- **Recommandation** (mise à jour, mitigation)

!!! tip "Prioritisation"
    Traiter en priorité :
    1. CVE CRITICAL dans les services exposés (awx_web, traefik, gitea)
    2. CVE HIGH avec exploit disponible
    3. Les autres à la prochaine fenêtre de maintenance
