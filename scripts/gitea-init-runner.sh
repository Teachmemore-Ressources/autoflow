#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# Autoflow — Enregistrement du Gitea Actions Runner
#
# Ce script :
#   1. Attend que Gitea soit disponible
#   2. Crée un token d'enregistrement runner via l'API Gitea
#   3. Écrit GITEA_RUNNER_TOKEN dans .env
#   4. Redémarre le conteneur act_runner pour qu'il se connecte
#
# Usage :
#   bash scripts/gitea-init-runner.sh
#   make gitea-init-runner
# ─────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Charger .env
if [[ -f "${PROJECT_ROOT}/.env" ]]; then
  set -a; source "${PROJECT_ROOT}/.env"; set +a
fi

# Use the external URL (via Traefik) — port 3001 is NOT bound to the host
GITEA_URL="${GITEA_ROOT_URL:-https://git.teachmemore.lan}"
GITEA_USER="${GITEA_ADMIN_USER:-admin}"
GITEA_PASS="${GITEA_ADMIN_PASSWORD:-}"
ENV_FILE="${PROJECT_ROOT}/.env"
# -k: skip cert verify (custom CA may not be in system store yet)
CURL="curl -sk"

log()  { echo -e "  \033[36m[runner-init]\033[0m $*"; }
ok()   { echo -e "  \033[32m✔\033[0m $*"; }
warn() { echo -e "  \033[33m⚠\033[0m $*"; }
err()  { echo -e "  \033[31m✖\033[0m $*" >&2; exit 1; }

[[ -z "${GITEA_PASS}" ]] && err "GITEA_ADMIN_PASSWORD vide dans .env"

# ── 1. Attendre Gitea ──────────────────────────────────────────
log "Attente de Gitea..."
for i in $(seq 1 30); do
  if $CURL --max-time 5 "${GITEA_URL}/api/v1/version" >/dev/null 2>&1; then
    ok "Gitea disponible."; break
  fi
  [[ $i -eq 30 ]] && err "Gitea non disponible après 60s."
  sleep 2
done

# ── 2. Récupérer un token d'enregistrement runner ──────────────
# NOTE: Gitea 1.21+ — endpoint is GET (not POST)
log "Récupération du token d'enregistrement runner..."

RESP=$($CURL -u "${GITEA_USER}:${GITEA_PASS}" \
  -H "Content-Type: application/json" \
  "${GITEA_URL}/api/v1/admin/runners/registration-token")

RUNNER_TOKEN=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))" 2>/dev/null || true)

if [[ -z "${RUNNER_TOKEN}" ]]; then
  err "Impossible d'obtenir le token runner. Réponse : ${RESP}"
fi

ok "Token runner obtenu."

# ── 3. Écrire GITEA_RUNNER_TOKEN dans .env ─────────────────────
log "Mise à jour de .env (GITEA_RUNNER_TOKEN)..."

if grep -q "^GITEA_RUNNER_TOKEN=" "${ENV_FILE}" 2>/dev/null; then
  # Mettre à jour la valeur existante
  sed -i "s|^GITEA_RUNNER_TOKEN=.*|GITEA_RUNNER_TOKEN=${RUNNER_TOKEN}|" "${ENV_FILE}"
else
  # Ajouter à la fin du fichier
  echo "" >> "${ENV_FILE}"
  echo "GITEA_RUNNER_TOKEN=${RUNNER_TOKEN}" >> "${ENV_FILE}"
fi
ok "GITEA_RUNNER_TOKEN écrit dans .env"

# ── 4. Recréer act_runner avec le nouveau token ─────────────────
# IMPORTANT: `restart` ne relit PAS les variables d'environnement.
# `up -d --force-recreate` recrée le conteneur avec les nouvelles env vars.
log "Recréation du conteneur act_runner (nouveau token)..."

cd "${PROJECT_ROOT}"
docker compose --env-file "${ENV_FILE}" up -d --force-recreate act_runner 2>&1 | tail -5
ok "act_runner recréé avec le nouveau token."

# ── 5. Vérifier l'enregistrement ────────────────────────────────
sleep 5
log "Vérification des runners enregistrés..."

RUNNERS=$($CURL -u "${GITEA_USER}:${GITEA_PASS}" \
  "${GITEA_URL}/api/v1/admin/runners?limit=10" 2>/dev/null || echo "{}")

COUNT=$(echo "$RUNNERS" | python3 -c "
import sys,json
d=json.load(sys.stdin)
runners=d if isinstance(d,list) else d.get('data',[])
print(len([r for r in runners if r.get('name','')=='autoflow-runner']))
" 2>/dev/null || echo "0")

echo ""
if [[ "${COUNT}" -gt 0 ]]; then
  ok "Runner 'autoflow-runner' enregistré dans Gitea ✔"
else
  warn "Runner pas encore visible (il peut prendre ~10s pour s'enregistrer)."
  warn "Vérifie dans Gitea : Site Administration → Actions → Runners"
fi

echo ""
echo "  ╔══════════════════════════════════════════════════════╗"
echo "  ║  Gitea Actions Runner configuré                     ║"
echo "  ╠══════════════════════════════════════════════════════╣"
echo "  ║  Labels : autoflow, linux, docker, ansible           ║"
echo "  ║  Vérifier : ${GITEA_URL}/-/admin/runners"
echo "  ╠══════════════════════════════════════════════════════╣"
echo "  ║  Prochaine étape :                                   ║"
echo "  ║  make gitea-init-network  (push workflows CI)        ║"
echo "  ╚══════════════════════════════════════════════════════╝"
echo ""
