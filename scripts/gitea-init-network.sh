#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# Autoflow — Initialisation du repo Gitea network-playbooks
#
# Ce script :
#   1. Attend que Gitea soit disponible
#   2. Crée le repo "network-playbooks" via l'API Gitea
#   3. Initialise un dépôt git local et pousse les playbooks
#
# Usage :
#   bash scripts/gitea-init-network.sh
#
# Variables d'environnement (depuis .env si disponible) :
#   GITEA_ROOT_URL    URL Gitea (défaut: http://localhost:3001)
#   GITEA_ADMIN_USER  Login admin Gitea (défaut: admin)
#   GITEA_ADMIN_PASS  Mot de passe admin (défaut: depuis .env)
# ─────────────────────────────────────────────────────────────

set -euo pipefail

# ── Charger .env si disponible ────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

if [[ -f "${PROJECT_ROOT}/.env" ]]; then
  # shellcheck disable=SC1091
  set -a; source "${PROJECT_ROOT}/.env"; set +a
fi

# ── Variables ─────────────────────────────────────────────────
GITEA_URL="${GITEA_ROOT_URL:-http://localhost:3001}"
GITEA_USER="${GITEA_ADMIN_USER:-admin}"
GITEA_PASS="${GITEA_ADMIN_PASSWORD:-${GITEA_PASS:-changeme}}"
REPO_NAME="network-playbooks"
PLAYBOOKS_SRC="${PROJECT_ROOT}/playbooks/network"
WORK_DIR="/tmp/autoflow-gitea-init-$$"

log()  { echo -e "  \033[36m[gitea-init]\033[0m $*"; }
ok()   { echo -e "  \033[32m✔\033[0m $*"; }
warn() { echo -e "  \033[33m⚠\033[0m $*"; }
err()  { echo -e "  \033[31m✖\033[0m $*" >&2; exit 1; }

# ── Attendre Gitea ────────────────────────────────────────────
log "Attente de Gitea sur ${GITEA_URL}..."
for i in $(seq 1 30); do
  if curl -sf --max-time 3 "${GITEA_URL}/api/v1/version" >/dev/null 2>&1; then
    ok "Gitea disponible."
    break
  fi
  if [[ $i -eq 30 ]]; then
    err "Gitea non disponible après 30 tentatives. Abandon."
  fi
  sleep 2
done

# ── Créer le repo ─────────────────────────────────────────────
log "Vérification du repo '${REPO_NAME}'..."

HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
  -u "${GITEA_USER}:${GITEA_PASS}" \
  "${GITEA_URL}/api/v1/repos/${GITEA_USER}/${REPO_NAME}")

if [[ "${HTTP_CODE}" == "200" ]]; then
  warn "Le repo '${REPO_NAME}' existe déjà — mise à jour du contenu."
else
  log "Création du repo '${REPO_NAME}'..."
  RESP=$(curl -s -X POST \
    -u "${GITEA_USER}:${GITEA_PASS}" \
    -H "Content-Type: application/json" \
    "${GITEA_URL}/api/v1/user/repos" \
    -d "{
      \"name\":         \"${REPO_NAME}\",
      \"description\":  \"Autoflow — Playbooks d'automatisation réseau (Cisco, Juniper, Arista, F5)\",
      \"private\":       false,
      \"auto_init\":     false,
      \"default_branch\":\"main\",
      \"readme\":        \"\"
    }")

  REPO_ID=$(echo "$RESP" | grep -o '"id":[0-9]*' | head -1 | cut -d: -f2 || true)
  if [[ -z "${REPO_ID}" ]]; then
    err "Impossible de créer le repo. Réponse Gitea : ${RESP}"
  fi
  ok "Repo '${REPO_NAME}' créé (id=${REPO_ID})."
fi

# ── Pousser les playbooks ─────────────────────────────────────
log "Initialisation du dépôt git local dans ${WORK_DIR}..."
rm -rf "${WORK_DIR}"
mkdir -p "${WORK_DIR}"

# Copier les playbooks
cp -r "${PLAYBOOKS_SRC}/." "${WORK_DIR}/"

cd "${WORK_DIR}"
git init -b main
git config user.email "autoflow@autoflow.local"
git config user.name  "Autoflow Bot"

# .gitignore
cat > .gitignore <<'EOF'
*.pyc
__pycache__/
*.retry
/tmp/
/backups/
*.log
EOF

git add .
git commit -m "feat: bibliothèque de playbooks réseau Autoflow

Collections : cisco.ios, cisco.nxos, junipernetworks.junos,
arista.eos, f5networks.f5_modules, ansible.netcommon
Libs : NAPALM, Netmiko, Nornir, ntc-templates

Playbooks :
- Cisco IOS : facts, backup, vlan, compliance
- Juniper Junos : facts, backup
- Arista EOS : facts, backup
- F5 BIG-IP : facts, pool management
- Compliance : config drift, security baseline"

# Push
PUSH_URL="${GITEA_URL/http:\/\//http:\/\/${GITEA_USER}:${GITEA_PASS}@}"
PUSH_URL="${PUSH_URL}//${GITEA_USER}/${REPO_NAME}.git"
# Reconstruire proprement
PUSH_URL="http://${GITEA_USER}:${GITEA_PASS}@${GITEA_URL#http://}/${GITEA_USER}/${REPO_NAME}.git"

log "Push vers ${GITEA_URL}/${GITEA_USER}/${REPO_NAME}..."
git remote add origin "${PUSH_URL}"

if git push -u origin main --force 2>&1; then
  ok "Playbooks poussés vers Gitea ✔"
else
  # Cas où le repo existe et a déjà des commits
  git pull origin main --allow-unrelated-histories --rebase 2>/dev/null || true
  git push -u origin main --force
  ok "Playbooks mis à jour sur Gitea ✔"
fi

# ── Nettoyage ─────────────────────────────────────────────────
cd /
rm -rf "${WORK_DIR}"

echo ""
ok "Repo '${REPO_NAME}' prêt sur ${GITEA_URL}/${GITEA_USER}/${REPO_NAME}"
echo ""
echo "  Prochaines étapes dans AWX :"
echo "  1. Créer un projet AWX → SCM Type: Git"
echo "     URL: ${GITEA_URL}/${GITEA_USER}/${REPO_NAME}"
echo "  2. Créer un Job Template → EE: ee-network"
echo "  3. Sélectionner le playbook désiré"
echo ""
