---
title: ADR-005 — PKI interne
---

# ADR-005 — PKI interne plutôt que Let's Encrypt

**Statut** : Accepté  
**Date** : 2024

---

## Contexte

Autoflow doit sécuriser ses communications avec TLS. Deux approches principales étaient envisageables :

1. **Let's Encrypt** : certificats publics gratuits, renouvellement automatique
2. **PKI interne** : autorité de certification privée gérée localement

Le contexte de déploiement cible inclut :

- Serveurs en réseau privé sans IP publique
- Environnements air-gapped (sans accès internet)
- Domaines internes non résolvables sur internet (`*.lan`, `*.internal`, `*.local`)
- Nécessité de contrôle total sur les certificats

---

## Décision

Utiliser une **PKI interne** (autorité de certification auto-signée) exposée via un service dédié à `https://pki.<DOMAIN>`.

---

## Alternatives considérées

### Let's Encrypt (ACME)

- ✅ Gratuit, renouvellement automatique
- ✅ Reconnu par tous les navigateurs nativement
- ❌ **Nécessite un accès internet** et un domaine DNS public
- ❌ Incompatible avec les environnements air-gapped
- ❌ Nécessite port 80 ou DNS challenge (complique le setup)
- ❌ Pas utilisable avec des domaines `.lan` / `.internal`
- ❌ Rate limits (50 certificats/semaine pour un domaine)

### ZeroSSL

- Mêmes contraintes que Let's Encrypt

### Certificats auto-signés par service

- ✅ Simple à générer
- ❌ Un certificat différent par service → gestion complexe
- ❌ Pas de point de confiance unique
- ❌ Difficile à distribuer dans les trust stores

### Vault PKI (HashiCorp)

- ✅ PKI très avancée avec rotation, révocation, audit
- ✅ Intégration Traefik via plugin
- ❌ Dépendance lourde (Vault lui-même nécessite opération)
- ❌ Complexité disproportionnée pour un single-node
- ❌ Nécessite un système de stockage backend (Consul, Raft)

### step-ca (Smallstep)

- ✅ PKI moderne, ACME interne supporté
- ✅ Plus léger que Vault
- ⚠️ Alternative sérieuse — complexité légèrement supérieure à la solution retenue

---

## Conséquences

**Avantages** :

- Fonctionne **sans accès internet** et en air-gapped
- Un **seul CA** à distribuer dans les trust stores (OS, navigateurs, containers)
- Certificats pour n'importe quel sous-domaine, y compris wildcards
- Validité configurable (1 an par défaut, PKI configurée à 1 an)
- Révocation via l'interface PKI
- API REST pour l'automatisation (renouvellement, émission)
- Intégration Traefik via fichiers cert (montés dans `traefik/certs/`)

**Inconvénients** :

- Le CA doit être **distribué manuellement** dans les trust stores (navigateurs, OS, containers)
- Alertes d'expiration à surveiller (règle Prometheus à 30 jours)
- Pas de reconnaissance publique (OK pour des services internes)

**Procédure de confiance** :

```bash
# Installer le CA dans Ubuntu/Debian
curl -sk https://pki.<DOMAIN>/api/v1/ca/cert -o /usr/local/share/ca-certificates/autoflow-ca.crt
sudo update-ca-certificates

# Vérifier
curl -sv https://awx.<DOMAIN>/api/v2/ping/ 2>&1 | grep "certificate"
```

**Décisions liées** :

- Traefik monte les certificats depuis `traefik/certs/*.crt` et `*.key`
- Le Deploy Wizard génère le CA et les certs au premier déploiement
- Une alerte Prometheus se déclenche 30 jours avant expiration
