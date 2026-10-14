#!/usr/bin/env bash
# assume-operator.sh — credențiale temporare pentru rolul de operator al proiectului.
#
# Folosit când rolul cere MFA la asumare (providerul Terraform nu poate trimite tokenul MFA)
# sau când vrei o sesiune de scurtă durată fără să scrii un profil în ~/.aws/config.
#
#   eval "$(./scripts/assume-operator.sh 123456)"   # codul MFA ca argument
#   eval "$(./scripts/assume-operator.sh)"          # cere codul, fără ecou
#
# Pe ieșirea standard ies doar instrucțiuni `export`, ca rezultatul să poată fi evaluat
# direct. Mesajele ajung pe ieșirea de eroare, ca să nu strice `eval`.
#
# Variabile citite din `.env` sau din mediu:
#   ACCESS_PROFILE (profilul sursă, al identității de admin), AWS_REGION, PROJECT_NAME,
#   ENVIRONMENT, OPERATOR_ROLE_ARN, OPERATOR_MFA_SERIAL, OPERATOR_SESSION_HOURS, SESSION_NAME.

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ -f "${ROOT}/.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "${ROOT}/.env"
  set +a
fi

PROJECT_NAME="${PROJECT_NAME:-cncdt}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
AWS_REGION="${AWS_REGION:-eu-central-1}"
SOURCE_PROFILE="${SOURCE_PROFILE:-${ACCESS_PROFILE:-default}}"
OPERATOR_ROLE_ARN="${OPERATOR_ROLE_ARN:-}"
OPERATOR_MFA_SERIAL="${OPERATOR_MFA_SERIAL:-}"
OPERATOR_SESSION_HOURS="${OPERATOR_SESSION_HOURS:-4}"
SESSION_NAME="${SESSION_NAME:-$(whoami)-$(date +%Y%m%d-%H%M%S)}"

log() { printf '%s\n' "$*" >&2; }
die() { printf 'eroare: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
assume-operator.sh — credențiale temporare pentru rolul de operator.

  eval "$(./scripts/assume-operator.sh 123456)"   codul MFA ca argument
  eval "$(./scripts/assume-operator.sh)"          cere codul MFA interactiv

Variabile: ACCESS_PROFILE (profilul de admin), OPERATOR_ROLE_ARN, OPERATOR_MFA_SERIAL,
OPERATOR_SESSION_HOURS, SESSION_NAME. Se citesc din `.env` dacă există.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

command -v aws >/dev/null 2>&1 || die "lipsește AWS CLI v2"
command -v jq >/dev/null 2>&1 || die "lipsește jq"

case "${OPERATOR_SESSION_HOURS}" in
  ''|*[!0-9]*) die "OPERATOR_SESSION_HOURS trebuie să fie un număr întreg de ore" ;;
esac
if [[ "${OPERATOR_SESSION_HOURS}" -lt 1 || "${OPERATOR_SESSION_HOURS}" -gt 12 ]]; then
  die "OPERATOR_SESSION_HOURS trebuie să fie între 1 și 12"
fi

aws_cli() {
  if [[ -n "${SOURCE_PROFILE}" && "${SOURCE_PROFILE}" != "default" ]]; then
    aws --profile "${SOURCE_PROFILE}" --region "${AWS_REGION}" "$@"
  else
    aws --region "${AWS_REGION}" "$@"
  fi
}

caller_arn="$(aws_cli sts get-caller-identity --query Arn --output text 2>/dev/null)" \
  || die "nu am putut citi identitatea curentă; verificați profilul '${SOURCE_PROFILE}'"

# arn:<partiție>:<serviciu>::<cont>:<resursă>
partition="$(printf '%s' "${caller_arn}" | cut -d: -f2)"
account_id="$(printf '%s' "${caller_arn}" | cut -d: -f5)"
[[ -n "${account_id}" ]] || die "nu am putut determina ID-ul de cont din ${caller_arn}"

if [[ -z "${OPERATOR_ROLE_ARN}" ]]; then
  OPERATOR_ROLE_ARN="arn:${partition}:iam::${account_id}:role/${PROJECT_NAME}-${ENVIRONMENT}-operator"
fi

log "identitate sursă: ${caller_arn}"
log "rol țintă:        ${OPERATOR_ROLE_ARN}"

# O sursă temporară (inclusiv sesiunea scrisă de `aws login`) face din asumare un lanț de roluri,
# iar AWS limitează lanțul la o oră, oricât de mare ar fi durata maximă a rolului. Detectăm sursa
# și reducem durata, ca să nu eșuăm cu „role chaining".
if [[ "${OPERATOR_SESSION_HOURS}" -gt 1 ]]; then
  if [[ -n "${AWS_SESSION_TOKEN:-}" ]] \
    || aws_cli configure export-credentials --format process 2>/dev/null \
      | jq -e '.SessionToken // "" | length > 0' >/dev/null 2>&1; then
    log "sursa de credențiale este temporară: asumarea este un lanț de roluri, limitat la 1 oră"
    log "durata cerută (${OPERATOR_SESSION_HOURS}h) se reduce la 1h"
    OPERATOR_SESSION_HOURS=1
  fi
fi

token=""
if [[ -n "${OPERATOR_MFA_SERIAL}" ]]; then
  token="${1:-}"
  if [[ -z "${token}" ]]; then
    printf 'Cod MFA pentru %s: ' "${OPERATOR_MFA_SERIAL}" >&2
    read -r -s token
    printf '\n' >&2
  fi
  [[ -n "${token}" ]] || die "codul MFA lipsește"
fi

assume_args=(
  sts assume-role
  --role-arn "${OPERATOR_ROLE_ARN}"
  --role-session-name "${SESSION_NAME}"
  --duration-seconds "$((OPERATOR_SESSION_HOURS * 3600))"
)
if [[ -n "${OPERATOR_MFA_SERIAL}" ]]; then
  assume_args+=(--serial-number "${OPERATOR_MFA_SERIAL}" --token-code "${token}")
fi

credentials="$(aws_cli "${assume_args[@]}" 2>/dev/null)" \
  || die "asumarea a eșuat; verificați trust-ul rolului, MFA-ul și numele rolului"

access_key="$(printf '%s' "${credentials}" | jq -r '.Credentials.AccessKeyId')"
secret_key="$(printf '%s' "${credentials}" | jq -r '.Credentials.SecretAccessKey')"
session_token="$(printf '%s' "${credentials}" | jq -r '.Credentials.SessionToken')"
expiration="$(printf '%s' "${credentials}" | jq -r '.Credentials.Expiration')"

[[ "${access_key}" != "null" ]] || die "răspuns neașteptat de la sts assume-role"

# Credențialele AWS conțin doar caractere sigure în ghilimele simple.
printf "export AWS_ACCESS_KEY_ID='%s'\n" "${access_key}"
printf "export AWS_SECRET_ACCESS_KEY='%s'\n" "${secret_key}"
printf "export AWS_SESSION_TOKEN='%s'\n" "${session_token}"

log "sesiune '${SESSION_NAME}' valabilă până la ${expiration}"
log "variabilele exportate au prioritate față de profilul din AWS_PROFILE"
