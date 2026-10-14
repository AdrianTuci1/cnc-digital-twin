#!/usr/bin/env bash
# tf-backend.sh — pregătește backend-ul de state pentru stack-ul `platform`.
#
# Creează bucket-ul S3 de state (stack-ul `bootstrap`, state local) și scrie
# infra/terraform/stacks/platform/envs/<mediu>.backend.hcl cu numele real al bucket-ului.
# Idempotent: dacă fișierul există și conține un nume concret, nu mai atinge AWS.
#
#   ./scripts/tf-backend.sh                    # mediul din .env (implicit dev)
#   ENVIRONMENT=staging ./scripts/tf-backend.sh
#
# Variabile citite din .env: PROJECT_NAME, ENVIRONMENT, AWS_REGION, AWS_PROFILE, TF_STATE_BUCKET.

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BOOTSTRAP_DIR="${ROOT}/infra/terraform/bootstrap"
PLATFORM_ENVS="${ROOT}/infra/terraform/stacks/platform/envs"

info() { printf '==> %s\n' "$*"; }
ok()   { printf ' ok %s\n' "$*"; }
die()  { printf 'eroare: %s\n' "$*" >&2; exit 1; }

if [[ -f "${ROOT}/.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "${ROOT}/.env"
  set +a
fi

PROJECT_NAME="${PROJECT_NAME:-cncdt}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
AWS_REGION="${AWS_REGION:-eu-central-1}"
AWS_PROFILE="${AWS_PROFILE:-default}"
TF_STATE_BUCKET="${TF_STATE_BUCKET:-}"

BACKEND_FILE="${PLATFORM_ENVS}/${ENVIRONMENT}.backend.hcl"

# Are nevoie de bootstrap dacă lipsește configurația sau are încă un nume de completat.
needs_bootstrap() {
  [[ ! -f "${BACKEND_FILE}" ]] && return 0
  if grep -q "REPLACE" "${BACKEND_FILE}"; then
    return 0
  fi
  if grep -qE '^[[:space:]]*bucket[[:space:]]*=[[:space:]]*""' "${BACKEND_FILE}"; then
    return 0
  fi
  return 1
}

if ! needs_bootstrap; then
  ok "backend deja configurat: ${BACKEND_FILE#"${ROOT}/"}"
  exit 0
fi

command -v terraform >/dev/null 2>&1 || die "lipsește comanda \`terraform\`"
command -v aws >/dev/null 2>&1 || die "lipsește AWS CLI v2"

info "inițializez stack-ul bootstrap (${BOOTSTRAP_DIR#"${ROOT}/"})"
terraform -chdir="${BOOTSTRAP_DIR}" init -input=false >/dev/null

info "creez bucket-ul de state în ${AWS_REGION} (profil ${AWS_PROFILE})"
apply_args=(-input=false -auto-approve -var "project_name=${PROJECT_NAME}" -var "region=${AWS_REGION}")
if [[ -n "${AWS_PROFILE}" && "${AWS_PROFILE}" != "default" ]]; then
  apply_args+=(-var "aws_profile=${AWS_PROFILE}")
fi
if [[ -n "${TF_STATE_BUCKET}" ]]; then
  apply_args+=(-var "state_bucket_name=${TF_STATE_BUCKET}")
fi

terraform -chdir="${BOOTSTRAP_DIR}" apply "${apply_args[@]}"

bucket="$(terraform -chdir="${BOOTSTRAP_DIR}" output -raw state_bucket_name 2>/dev/null || true)"
[[ -n "${bucket}" ]] || die "nu am putut citi numele bucket-ului de state"

mkdir -p "${PLATFORM_ENVS}"
info "scriu ${BACKEND_FILE#"${ROOT}/"}"
cat > "${BACKEND_FILE}" <<EOF
# Generat de scripts/tf-backend.sh. Nu editați manual: se rescrie la nevoie.
bucket       = "${bucket}"
key          = "platform/${ENVIRONMENT}/terraform.tfstate"
region       = "${AWS_REGION}"
encrypt      = true
use_lockfile = true
EOF

ok "backend gata: s3://${bucket}/platform/${ENVIRONMENT}/terraform.tfstate"
