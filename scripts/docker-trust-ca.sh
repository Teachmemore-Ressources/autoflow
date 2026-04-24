#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# Autoflow — Configurer Docker pour faire confiance au CA PKI
#
# Ce script :
#   1. Récupère le certificat CA depuis le service PKI Autoflow
#   2. L'installe dans /etc/docker/certs.d/<registry>/ca.crt
#   3. L'installe dans le trust store système (update-ca-certificates)
#   4. Configure insecure-registries en fallback si nécessaire
#
# Usage :
#   bash scripts/docker-trust-ca.sh
#   make docker-trust-ca
#
# Prérequis : les services Autoflow doivent être démarrés (make start)
# ─────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Charger .env
if [[ -f "${PROJECT_ROOT}/.env" ]]; then
  set -a; source "${PROJECT_ROOT}/.env"; set +a
fi

DOMAIN="${DOMAIN:-localhost}"
REGISTRY="git.${DOMAIN}"
PKI_URL="${PKI_URL:-https://pki.${DOMAIN}}"
# Fallback HTTP pour récupérer le CA si le domaine n'est pas encore résolu
PKI_LOCAL_URL="http://localhost:${PKI_PORT:-8004}"

log()  { echo -e "  \033[36m[docker-trust]\033[0m $*"; }
ok()   { echo -e "  \033[32m✔\033[0m $*"; }
warn() { echo -e "  \033[33m⚠\033[0m $*"; }
err()  { echo -e "  \033[31m✖\033[0m $*" >&2; exit 1; }

# ── 1. Récupérer le CA depuis le PKI ─────────────────────────

log "Récupération du CA Autoflow depuis le service PKI..."

CA_CERT=""
PKI_CA_NAME=""

# ── Essai 1 : API PKI via domaine Traefik (HTTPS) ────────────
_pki_list_url="${PKI_URL}/api/ca/list"
if curl -sk --max-time 5 "${_pki_list_url}" -o /dev/null 2>/dev/null; then
  PKI_CA_NAME=$(curl -sk "${_pki_list_url}" | python3 -c "
import sys,json
cas=json.load(sys.stdin)
if isinstance(cas,list) and cas:
    print(cas[0].get('name',''))
elif isinstance(cas,dict):
    items=cas.get('cas',cas.get('results',[]))
    if items: print(items[0].get('name',''))
" 2>/dev/null || true)
fi

# ── Essai 2 : port local direct (HTTP) ───────────────────────
if [[ -z "${PKI_CA_NAME}" ]]; then
  log "Essai via port local ${PKI_LOCAL_URL}..."
  PKI_CA_NAME=$(curl -s --max-time 5 "${PKI_LOCAL_URL}/api/ca/list" | python3 -c "
import sys,json
cas=json.load(sys.stdin)
if isinstance(cas,list) and cas:
    print(cas[0].get('name',''))
elif isinstance(cas,dict):
    items=cas.get('cas',cas.get('results',[]))
    if items: print(items[0].get('name',''))
" 2>/dev/null || true)
fi

# ── Télécharger le PEM du CA trouvé ──────────────────────────
if [[ -n "${PKI_CA_NAME}" ]]; then
  log "CA trouvé : '${PKI_CA_NAME}' — téléchargement du PEM..."
  for _base_url in "${PKI_LOCAL_URL}" "${PKI_URL}"; do
    _cert=$(curl -sk --max-time 10 "${_base_url}/api/ca/${PKI_CA_NAME}/cert.pem" 2>/dev/null || true)
    if echo "${_cert}" | grep -q "BEGIN CERTIFICATE"; then
      CA_CERT="${_cert}"
      break
    fi
  done
fi

# ── Essai 3 : fichier cert wildcard local (Traefik) ──────────
if [[ -z "${CA_CERT}" ]]; then
  CERT_FILE="${PROJECT_ROOT}/traefik/certs/wildcard.${DOMAIN}.crt"
  if [[ -f "${CERT_FILE}" ]]; then
    warn "PKI non disponible — utilisation du cert wildcard Traefik local."
    CA_CERT=$(cat "${CERT_FILE}")
  fi
fi

if [[ -z "${CA_CERT}" ]]; then
  err "Impossible de récupérer le certificat CA.
       Vérifie que le service PKI est démarré : make logs SERVICES=pki
       Ou lance d'abord : make start"
fi

ok "Certificat CA récupéré${PKI_CA_NAME:+ (CA: ${PKI_CA_NAME})} — ${#CA_CERT} caractères."

# ── 2. Installer dans /etc/docker/certs.d/ ───────────────────

DOCKER_CERT_DIR="/etc/docker/certs.d/${REGISTRY}"

log "Installation dans ${DOCKER_CERT_DIR}/ca.crt ..."
sudo mkdir -p "${DOCKER_CERT_DIR}"
echo "${CA_CERT}" | sudo tee "${DOCKER_CERT_DIR}/ca.crt" > /dev/null
sudo chmod 644 "${DOCKER_CERT_DIR}/ca.crt"
ok "cert installé dans ${DOCKER_CERT_DIR}/ca.crt"

# ── 3. Installer dans le trust store système ──────────────────

log "Installation dans le trust store système..."
if command -v update-ca-certificates >/dev/null 2>&1; then
  # Debian/Ubuntu
  echo "${CA_CERT}" | sudo tee "/usr/local/share/ca-certificates/autoflow-ca.crt" > /dev/null
  sudo update-ca-certificates --fresh 2>/dev/null | tail -3
  ok "Trust store système mis à jour (Debian/Ubuntu)."
elif command -v update-ca-trust >/dev/null 2>&1; then
  # RHEL/CentOS/Fedora
  echo "${CA_CERT}" | sudo tee "/etc/pki/ca-trust/source/anchors/autoflow-ca.crt" > /dev/null
  sudo update-ca-trust extract
  ok "Trust store système mis à jour (RHEL/CentOS)."
else
  warn "update-ca-certificates non trouvé — trust store système non mis à jour."
fi

# ── 4. Redémarrer le daemon Docker ────────────────────────────

log "Redémarrage du daemon Docker pour charger le nouveau CA..."
if sudo systemctl restart docker 2>/dev/null; then
  ok "Docker daemon redémarré ✔ (les conteneurs Autoflow reviennent automatiquement)"
  sleep 5  # laisser le daemon démarrer avant les tests
else
  warn "Redémarrage Docker échoué — relance manuellement : sudo systemctl restart docker"
fi

# ── 5. Afficher la configuration Docker daemon ────────────────

DAEMON_JSON="/etc/docker/daemon.json"

# Vérifier si la configuration existe déjà
if sudo test -f "${DAEMON_JSON}"; then
  CURRENT=$(sudo cat "${DAEMON_JSON}")
else
  CURRENT="{}"
fi

# Vérifier si insecure-registries est nécessaire (en cas d'échec TLS)
# Pour l'instant, avec le CA installé, ce n'est pas nécessaire.
# On affiche juste l'info.
log "Configuration Docker actuelle :"
sudo cat "${DAEMON_JSON}" 2>/dev/null || echo "  (aucune)"

# ── 6. Résolution DNS — ajouter /etc/hosts si nécessaire ──────

log "Vérification résolution DNS de ${REGISTRY}..."
if ! getent hosts "${REGISTRY}" >/dev/null 2>&1; then
  warn "${REGISTRY} non résolu — ajout dans /etc/hosts (127.0.0.1)..."
  HOSTS_LINE="127.0.0.1  ${REGISTRY}"
  if grep -qF "${REGISTRY}" /etc/hosts 2>/dev/null; then
    ok "Entrée déjà présente dans /etc/hosts pour ${REGISTRY}."
  else
    if echo "${HOSTS_LINE}" | sudo tee -a /etc/hosts >/dev/null; then
      ok "Ajouté dans /etc/hosts : ${HOSTS_LINE} ✔"
    else
      warn "Impossible d'écrire dans /etc/hosts."
      warn "Ajoute manuellement : echo '${HOSTS_LINE}' | sudo tee -a /etc/hosts"
    fi
  fi
else
  ok "${REGISTRY} se résout correctement."
fi

# ── 7. Test de connexion Docker + génération auto du token ────

echo ""
log "Test de connexion au registry ${REGISTRY}..."

_GITEA_USER="${GITEA_ADMIN_USER:-admin}"
_GITEA_PASS="${GITEA_ADMIN_PASSWORD:-}"
_GITEA_PORT="${GITEA_HTTP_PORT:-3001}"
_GITEA_API="http://localhost:${_GITEA_PORT}/api/v1"

_docker_login() {
  echo "$1" | docker login "${REGISTRY}" -u "${_GITEA_USER}" --password-stdin 2>&1
}

# Essai 1 : GITEA_REGISTRY_TOKEN existant
if [[ -n "${GITEA_REGISTRY_TOKEN:-}" ]]; then
  OUT=$(_docker_login "${GITEA_REGISTRY_TOKEN}")
  if echo "${OUT}" | grep -q "Login Succeeded"; then
    ok "docker login ${REGISTRY} → SUCCESS ✔"
  else
    warn "GITEA_REGISTRY_TOKEN invalide (${OUT##*: }) — génération d'un nouveau token..."
    GITEA_REGISTRY_TOKEN=""
  fi
fi

# Essai 2 : générer un token via l'API Gitea
if [[ -z "${GITEA_REGISTRY_TOKEN:-}" ]] && [[ -n "${_GITEA_PASS}" ]]; then
  log "Génération du token registry via l'API Gitea..."
  # Supprimer l'ancien token (ignore les erreurs)
  curl -s -X DELETE -u "${_GITEA_USER}:${_GITEA_PASS}" \
    "${_GITEA_API}/users/${_GITEA_USER}/tokens/autoflow-registry" >/dev/null 2>&1 || true
  # Créer un nouveau token
  TOKEN_RESP=$(curl -s -X POST \
    -u "${_GITEA_USER}:${_GITEA_PASS}" \
    -H "Content-Type: application/json" \
    -d '{"name":"autoflow-registry","scopes":["read:package","write:package"]}' \
    "${_GITEA_API}/users/${_GITEA_USER}/tokens")
  NEW_TOKEN=$(echo "${TOKEN_RESP}" | python3 -c \
    "import sys,json; print(json.load(sys.stdin).get('sha1',''))" 2>/dev/null || true)

  if [[ -n "${NEW_TOKEN}" ]]; then
    ok "Token généré — écriture dans .env (GITEA_REGISTRY_TOKEN)..."
    if grep -q "^GITEA_REGISTRY_TOKEN=" "${PROJECT_ROOT}/.env" 2>/dev/null; then
      sed -i "s|^GITEA_REGISTRY_TOKEN=.*|GITEA_REGISTRY_TOKEN=${NEW_TOKEN}|" "${PROJECT_ROOT}/.env"
    else
      echo "GITEA_REGISTRY_TOKEN=${NEW_TOKEN}" >> "${PROJECT_ROOT}/.env"
    fi
    OUT=$(_docker_login "${NEW_TOKEN}")
    if echo "${OUT}" | grep -q "Login Succeeded"; then
      ok "docker login ${REGISTRY} → SUCCESS ✔"
    else
      warn "docker login KO malgré le nouveau token : ${OUT##*: }"
    fi
  else
    warn "Impossible de générer le token (API Gitea dispo ?)"
    warn "Réponse : ${TOKEN_RESP:0:200}"
  fi
fi

# Essai 3 : mot de passe admin en dernier recours
if [[ -z "${GITEA_REGISTRY_TOKEN:-}" ]] && [[ -n "${_GITEA_PASS}" ]]; then
  OUT=$(_docker_login "${_GITEA_PASS}")
  if echo "${OUT}" | grep -q "Login Succeeded"; then
    ok "docker login avec mot de passe admin → SUCCESS ✔"
    warn "Génère un token dédié : Gitea > ${_GITEA_USER} > Settings > Applications"
  else
    warn "docker login KO — Gitea est-il démarré ?"
    warn "Connecte-toi manuellement : docker login ${REGISTRY} -u ${_GITEA_USER}"
  fi
fi

# ── Résumé ────────────────────────────────────────────────────
echo ""
echo "  ╔══════════════════════════════════════════════════════╗"
echo "  ║  Registry Docker configuré                          ║"
echo "  ╠══════════════════════════════════════════════════════╣"
echo "  ║  Registry  : ${REGISTRY}"
echo "  ║  CA cert   : ${DOCKER_CERT_DIR}/ca.crt"
echo "  ╠══════════════════════════════════════════════════════╣"
echo "  ║  Prochaines étapes :                                 ║"
echo "  ║  1. make ee-deps        (installer ansible-builder)  ║"
echo "  ║  2. make ee-network VERSION=1.0.0                    ║"
echo "  ╚══════════════════════════════════════════════════════╝"
echo ""
