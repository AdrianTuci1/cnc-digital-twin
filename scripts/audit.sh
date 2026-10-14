#!/usr/bin/env bash
# audit.sh — inventarul resurselor proiectului și verificări de igienă.
#
# Răspunde la trei întrebări:
#   1. Codul Terraform și realitatea din AWS mai coincid? (derivă)
#   2. Există resurse etichetate `ManagedBy=terraform` care nu apar în state?
#   3. Au rămas în urmă resurse care costă: EFS-ul Studio, aplicații Studio deschise,
#      endpoint-uri SageMaker, instanțe GPU?
#
# Coduri de ieșire:
#   0  totul curat
#   1  există elemente de verificat sau de curățat
#   2  audit parțial: lipsesc unelte sau credențiale, verificările AWS nu au rulat
#
# Utilizare:  make audit   sau   ./scripts/audit.sh

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TF_DIR="${ROOT}/infra/terraform"
PLATFORM_DIR="${TF_DIR}/stacks/platform"
SIM_DIR="${TF_DIR}/stacks/sim"
# Volumul de date persistent: stack separat, cu state local, ca `sim-down` să nu îl poată șterge.
DATA_DIR="${TF_DIR}/stacks/sim-data"
TWINCTL="${ROOT}/tools/twinctl/twinctl"

if [[ -f "${ROOT}/.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "${ROOT}/.env"
  set +a
fi

PROJECT_NAME="${PROJECT_NAME:-cncdt}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
AWS_REGION="${AWS_REGION:-eu-central-1}"
AWS_PROFILE="${AWS_PROFILE:-}"

PROBLEMS=0
PARTIAL=0
ACCOUNT_ID=""
AWS_READY=0

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

if [[ -t 1 ]]; then
  BOLD='\033[1m'; DIM='\033[2m'; RESET='\033[0m'
else
  BOLD=''; DIM=''; RESET=''
fi

# ---------------------------------------------------------------- ieșire
bold()    { printf "${BOLD}%s${RESET}\n" "$*"; }
dim()     { printf "${DIM}%s${RESET}\n" "$*"; }
section() { printf "\n${BOLD}== %s${RESET}\n" "$*"; }
ok()      { printf '  ok    %s\n' "$*"; }
info()    { printf '  ..    %s\n' "$*"; }
note()    { printf '  nota  %s\n' "$*"; }
problem() { printf '  ATENȚIE %s\n' "$*"; PROBLEMS=$((PROBLEMS + 1)); }
skipped() { printf '  ??    %s\n' "$*"; PARTIAL=$((PARTIAL + 1)); }
die()     { printf 'eroare: %s\n' "$*" >&2; exit 2; }

usage() {
  cat <<'EOF'
audit.sh — inventarul resurselor și verificări de igienă.

  ./scripts/audit.sh          rulează toate verificările
  ./scripts/audit.sh --help   afișează acest text

Verificările care cer AWS sunt sărite automat când lipsesc AWS CLI sau credențialele.
Variabilele PROJECT_NAME, ENVIRONMENT, AWS_REGION și AWS_PROFILE se citesc din `.env`.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

if [[ $# -gt 0 ]]; then
  die "argument necunoscut: $1 (folosiți --help)"
fi

aws_cli() {
  if [[ -n "${AWS_PROFILE}" && "${AWS_PROFILE}" != "default" ]]; then
    aws --profile "${AWS_PROFILE}" --region "${AWS_REGION}" "$@"
  else
    aws --region "${AWS_REGION}" "$@"
  fi
}

# ---------------------------------------------------------------- preflight
section "Preflight"

command -v terraform >/dev/null 2>&1 || die "lipsește comanda \`terraform\`"
ok "$(terraform version | head -1)"

bold "  proiect: ${PROJECT_NAME}, mediu: ${ENVIRONMENT}, regiune: ${AWS_REGION}"

if ! command -v aws >/dev/null 2>&1; then
  skipped "AWS CLI lipsește: verificările din AWS sunt sărite (instalați AWS CLI v2)"
elif ! command -v jq >/dev/null 2>&1; then
  skipped "jq lipsește: verificările din AWS sunt sărite"
elif ! aws_cli sts get-caller-identity >/dev/null 2>&1; then
  skipped "credențialele AWS nu răspund (profil '${AWS_PROFILE:-implicit}'): verificările din AWS sunt sărite"
else
  ACCOUNT_ID="$(aws_cli sts get-caller-identity --query Account --output text)"
  CALLER_ARN="$(aws_cli sts get-caller-identity --query Arn --output text)"
  AWS_READY=1
  ok "cont ${ACCOUNT_ID}"
  ok "identitate: ${CALLER_ARN}"
  case "${CALLER_ARN}" in
    *:user/*)
      info "sesiune de utilizator: resursele create acum apar în CloudTrail pe utilizator, nu pe rol"
      info "pentru atribuire pe rol: make access-up, make access-profile, apoi AWS_PROFILE=${PROFILE_NAME:-${PROJECT_NAME}}"
      info "detalii: ops/runbooks/access.md"
      ;;
  esac
fi

# ---------------------------------------------------------------- derivă Terraform
section "Derivă Terraform: cod vs. AWS"

PLATFORM_READY=0
BACKEND_FILE="${PLATFORM_DIR}/envs/${ENVIRONMENT}.backend.hcl"

if [[ ! -f "${BACKEND_FILE}" ]]; then
  info "backend-ul pentru '${ENVIRONMENT}' nu este scris: rulați \`make platform-backend\`"
elif grep -q "REPLACE" "${BACKEND_FILE}"; then
  info "backend-ul conține valori de completat: rulați \`make platform-backend\`"
elif [[ "${AWS_READY}" -eq 0 ]]; then
  info "platform: plan sărit, credențialele lipsesc"
else
  if terraform -chdir="${PLATFORM_DIR}" init -input=false \
    -backend-config="envs/${ENVIRONMENT}.backend.hcl" >"${TMP_DIR}/init.log" 2>&1; then
    PLATFORM_READY=1
  else
    problem "platform: \`terraform init\` a eșuat"
    tail -3 "${TMP_DIR}/init.log" | sed 's/^/        /'
  fi
fi

# Rezultatul planului este scris în PLAN_CODE, jurnalul în PLAN_LOG.
PLAN_CODE=0
PLAN_LOG=""

run_plan() {
  local dir="$1"
  shift

  PLAN_LOG="${TMP_DIR}/plan-$(basename "${dir}").log"
  set +e
  terraform -chdir="${dir}" plan -detailed-exitcode -input=false -lock=false -no-color "$@" \
    >"${PLAN_LOG}" 2>&1
  PLAN_CODE=$?
  set -e
}

report_plan() {
  local label="$1"

  case "${PLAN_CODE}" in
    0)
      ok "${label}: nicio schimbare față de cod"
      ;;
    2)
      problem "${label}: plan-ul propune modificări"
      grep -E '^  # ' "${PLAN_LOG}" 2>/dev/null | head -8 | sed 's/^/        /' || true
      dim "        lista completă: make platform-plan"
      ;;
    *)
      problem "${label}: plan-ul a eșuat"
      tail -5 "${PLAN_LOG}" | sed 's/^/        /'
      ;;
  esac
}

if [[ "${PLATFORM_READY}" -eq 1 ]]; then
  info "platform: $(terraform -chdir="${PLATFORM_DIR}" state list 2>/dev/null | wc -l | tr -d ' ') resurse în state"
  run_plan "${PLATFORM_DIR}" -var-file="envs/${ENVIRONMENT}.tfvars"
  report_plan "platform"
fi

if [[ "${AWS_READY}" -eq 0 ]]; then
  info "sim: plan sărit, credențialele lipsesc"
elif [[ -z "$(terraform -chdir="${SIM_DIR}" state list 2>/dev/null || true)" ]]; then
  info "sim: nicio resursă în state-ul local (instanța nu este lansată)"
else
  info "sim: $(terraform -chdir="${SIM_DIR}" state list 2>/dev/null | wc -l | tr -d ' ') resurse în state"
  if [[ ! -d "${SIM_DIR}/.terraform" ]] && ! terraform -chdir="${SIM_DIR}" init -input=false >/dev/null 2>&1; then
    problem "sim: \`terraform init\` a eșuat"
  else
    run_plan "${SIM_DIR}"
    report_plan "sim"
  fi
fi

if [[ "${AWS_READY}" -eq 0 ]]; then
  info "sim-data: plan sărit, credențialele lipsesc"
elif [[ -z "$(terraform -chdir="${DATA_DIR}" state list 2>/dev/null || true)" ]]; then
  info "sim-data: niciun volum de date în state (se plătește doar instanța)"
else
  info "sim-data: $(terraform -chdir="${DATA_DIR}" state list 2>/dev/null | wc -l | tr -d ' ') resurse în state"
  if [[ ! -d "${DATA_DIR}/.terraform" ]] && ! terraform -chdir="${DATA_DIR}" init -input=false >/dev/null 2>&1; then
    problem "sim-data: \`terraform init\` a eșuat"
  else
    # Zona e obligatorie pentru un volum; twinctl o scrie în sim-data.auto.tfvars. Dacă fișierul
    # lipsește, o luăm din state și completăm restul din .env, ca planul să rămână comparabil.
    PLAN_LOG=""
    data_az="$(terraform -chdir="${DATA_DIR}" output -raw availability_zone 2>/dev/null || true)"
    if [[ -f "${DATA_DIR}/sim-data.auto.tfvars" ]]; then
      run_plan "${DATA_DIR}"
    elif [[ -n "${data_az}" ]]; then
      run_plan "${DATA_DIR}" -var "availability_zone=${data_az}" \
        -var "size_gb=${SIM_DATA_VOLUME_GB:-100}" \
        -var "iops=${SIM_DATA_VOLUME_IOPS:-3000}" \
        -var "throughput=${SIM_DATA_VOLUME_THROUGHPUT:-125}"
    else
      info "sim-data: plan sărit (lipsește sim-data.auto.tfvars)"
    fi
    if [[ -n "${PLAN_LOG}" ]]; then report_plan "sim-data"; fi
  fi
  info "sim-data: volumul de date se plătește lunar; se șterge doar cu \`make sim-data-down\`"
fi

# ---------------------------------------------------------------- inventar
section "Inventar: resurse etichetate vs. state"

collect_state_arns() {
  terraform -chdir="${PLATFORM_DIR}" state pull 2>/dev/null || true
  terraform -chdir="${SIM_DIR}" state pull 2>/dev/null || true
  terraform -chdir="${DATA_DIR}" state pull 2>/dev/null || true
}

# Resursele create de servicii în numele nostru, care prin design nu apar în state.
known_service_owned() {
  case "$1" in
    *:elasticfilesystem:*|*:sagemaker:*:app/*|*:sagemaker:*:space/*) return 0 ;;
    *) return 1 ;;
  esac
}

if [[ "${AWS_READY}" -eq 0 ]]; then
  info "inventarul din AWS este sărit"
else
  collect_state_arns | jq -r '[.. | strings | select(startswith("arn:aws:"))] | unique | .[]' \
    2>/dev/null | sort -u >"${TMP_DIR}/state_arns.txt" || true

  : >"${TMP_DIR}/tagged.txt"
  token=""
  page=0
  fetch_ok=1

  while :; do
    page=$((page + 1))
    args=(resourcegroupstaggingapi get-resources --resources-per-page 100 --output json
      --tag-filters "Key=ManagedBy,Values=terraform" "Key=Project,Values=${PROJECT_NAME}")
    if [[ -n "${token}" ]]; then
      args+=(--pagination-token "${token}")
    fi
    if ! out="$(aws_cli "${args[@]}" 2>"${TMP_DIR}/tagged.err")"; then
      fetch_ok=0
      break
    fi
    jq -r '.ResourceTagMappingList[]?.ResourceARN' <<<"${out}" >>"${TMP_DIR}/tagged.txt"
    token="$(jq -r '.PaginationToken // empty' <<<"${out}")"
    if [[ -z "${token}" || "${page}" -ge 20 ]]; then
      break
    fi
  done

  if [[ "${fetch_ok}" -eq 0 ]]; then
    skipped "get-resources a eșuat: $(head -1 "${TMP_DIR}/tagged.err")"
  else
    sort -u -o "${TMP_DIR}/tagged.txt" "${TMP_DIR}/tagged.txt"
    tagged_count="$(wc -l <"${TMP_DIR}/tagged.txt" | tr -d ' ')"
    ok "${tagged_count} resurse etichetate ManagedBy=terraform și Project=${PROJECT_NAME}"

    : >"${TMP_DIR}/untracked.txt"
    while IFS= read -r arn; do
      [[ -z "${arn}" ]] && continue
      if grep -Fxq "${arn}" "${TMP_DIR}/state_arns.txt"; then
        continue
      fi
      known_service_owned "${arn}" && continue
      printf '%s\n' "${arn}" >>"${TMP_DIR}/untracked.txt"
    done <"${TMP_DIR}/tagged.txt"

    if [[ -s "${TMP_DIR}/untracked.txt" ]]; then
      problem "$(wc -l <"${TMP_DIR}/untracked.txt" | tr -d ' ') resurse etichetate nu apar în state:"
      sed 's/^/        /' "${TMP_DIR}/untracked.txt"
      dim "        ștergeți-le manual după ce confirmați că nu sunt folosite (vezi ops/runbooks/audit.md)"
    else
      ok "fiecare resursă etichetată apare în state"
    fi
  fi
fi

# ---------------------------------------------------------------- resturi
section "Resturi care costă"

if [[ "${AWS_READY}" -eq 0 ]]; then
  info "verificările din AWS sunt sărite"
else
  # --- EFS-ul de home al Studio: supraviețuiește prin design unui `terraform destroy`.
  efs_list="$(aws_cli efs describe-file-systems --output json 2>/dev/null \
    | jq -r --arg p "${PROJECT_NAME}" \
      '.FileSystems[]? | select((.Tags // []) | any(.Key == "Project" and .Value == $p))
       | "\(.FileSystemId)  \(.LifeCycleState)  \(.SizeInBytes.Value // 0) bytes"' || true)"
  if [[ -n "${efs_list}" ]]; then
    note "EFS-uri de proiect (nu sunt șterse de \`make down\`):"
    printf '%s\n' "${efs_list}" | sed 's/^/        /'
    efs_with_backup="$(aws_cli backup list-backup-plans --output json 2>/dev/null \
      | jq -r --arg p "${PROJECT_NAME}" '.BackupPlansList[]? | select(.BackupPlanName | startswith($p)) | .BackupPlanName' || true)"
    if [[ -n "${efs_with_backup}" ]]; then
      ok "plan de backup activ pentru EFS: ${efs_with_backup}"
    else
      problem "niciun plan de backup AWS nu acoperă EFS-ul de proiect"
    fi
  else
    ok "niciun EFS de proiect rămas"
  fi

  # --- Aplicații Studio deschise: blochează ștergerea domeniului și consumă compute.
  apps="$(aws_cli sagemaker list-apps --output json 2>/dev/null \
    | jq -r '.Apps[]? | select(.Status == "InService") | "\(.AppType) \(.AppName) [\(.Status)]"' || true)"
  if [[ -n "${apps}" ]]; then
    problem "aplicații SageMaker Studio în execuție (blochează \`terraform destroy\` al domeniului):"
    printf '%s\n' "${apps}" | sed 's/^/        /'
  else
    ok "nicio aplicație SageMaker Studio deschisă"
  fi

  # --- Endpoint-uri SageMaker: serverless costă doar la invocare, provisioned costă în repaus.
  endpoints="$(aws_cli sagemaker list-endpoints --output json 2>/dev/null \
    | jq -r --arg p "${PROJECT_NAME}-${ENVIRONMENT}-" \
      '.Endpoints[]? | select(.EndpointName | startswith($p)) | "\(.EndpointName) [\(.EndpointStatus)]"' || true)"
  if [[ -n "${endpoints}" ]]; then
    note "endpoint-uri SageMaker ale mediului (costă doar cele provisioned):"
    printf '%s\n' "${endpoints}" | sed 's/^/        /'
  else
    ok "niciun endpoint SageMaker al mediului"
  fi

  # --- Instanțe: o instanță GPU uitată pornită este cea mai scumpă greșeală.
  instances="$(aws_cli ec2 describe-instances \
    --filters "Name=tag:Project,Values=${PROJECT_NAME}" \
      "Name=instance-state-name,Values=running,stopped,pending,stopping" \
    --output json 2>/dev/null \
    | jq -r '.Reservations[].Instances[]? | "\(.InstanceId)  \(.InstanceType)  \(.State.Name)  \(.LaunchTime)"' || true)"
  if [[ -n "${instances}" ]]; then
    if printf '%s\n' "${instances}" | grep -q "running"; then
      problem "instanțe de proiect pornite (opriți-le cu \`make sim-stop\` dacă nu le folosiți):"
    else
      note "instanțe de proiect oprite (plătiți doar discurile):"
    fi
    printf '%s\n' "${instances}" | sed 's/^/        /'
  else
    ok "nicio instanță de proiect"
  fi

  # --- Costul instanței de simulare, calculat de twinctl din LaunchTime.
  if [[ -x "${TWINCTL}" ]]; then
    if cost="$(cd "${ROOT}" && "${TWINCTL}" cost 2>/dev/null)"; then
      printf '%s\n' "${cost}" | sed 's/^/        /'
    fi
  fi

  # --- Bugetul de cost: singurul control care prinde cheltuiala scăpată de alarme.
  budgets="$(aws_cli budgets describe-budgets --account-id "${ACCOUNT_ID}" --output json 2>/dev/null \
    | jq -r --arg p "${PROJECT_NAME}-" \
      '.Budgets[]? | select(.BudgetName | startswith($p))
       | "\(.BudgetName): \(.CalculatedSpend.ActualSpend.Amount // "0") / \(.BudgetLimit.Amount) \(.BudgetLimit.Unit)"' || true)"
  if [[ -n "${budgets}" ]]; then
    note "buget de cost:"
    printf '%s\n' "${budgets}" | sed 's/^/        /'
  else
    info "niciun buget de cost găsit pentru prefixul '${PROJECT_NAME}-'"
  fi
fi

# ---------------------------------------------------------------- rezumat
section "Rezumat"

if [[ "${PROBLEMS}" -eq 0 && "${PARTIAL}" -eq 0 ]]; then
  ok "curat: nicio derivă, niciun rest, nicio verificare sărită"
  exit 0
fi

printf '  %s element(e) de verificat, %s verificare(i) sărite\n' "${PROBLEMS}" "${PARTIAL}"

if [[ "${PARTIAL}" -gt 0 && "${PROBLEMS}" -eq 0 ]]; then
  dim "  audit parțial: instalați AWS CLI și credențialele, apoi rulați din nou"
  exit 2
fi

dim "  pașii de curățare: ops/runbooks/audit.md"
exit 1
