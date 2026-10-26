#!/bin/bash
# Aduce certificatele mTLS ale dispozitivului din state-ul Terraform în simulation/secrets/.
#
# Necesită ca stack-ul de platformă să fie aplicat, ca să existe ieșirile cu certificatul.
#
#   ./simulation/scripts/gen-iot-certs.sh [mediu]
#
# Fișierele rezultate sunt ignorate de git (simulation/secrets/).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENVIRONMENT="${1:-${ENVIRONMENT:-dev}}"
STACK_DIR="${ROOT}/infra/terraform/stacks/platform"
SECRETS_DIR="${ROOT}/simulation/secrets"
IOT_ROOT_CA_URL="https://www.amazontrust.com/repository/AmazonRootCA1.pem"

if [[ ! -d "${STACK_DIR}/.terraform" ]]; then
  echo "Stack-ul de platformă nu este inițializat: ${STACK_DIR}" >&2
  echo "Rulați: cd ${STACK_DIR#"${ROOT}/"} && terraform init -backend-config=envs/${ENVIRONMENT}.backend.hcl" >&2
  exit 1
fi

mkdir -p "${SECRETS_DIR}"
chmod 700 "${SECRETS_DIR}"

echo "==> citesc ieșirile Terraform pentru mediul ${ENVIRONMENT}"
if ! (cd "${STACK_DIR}" && terraform output -json >/dev/null 2>&1); then
  echo "Nu pot citi ieșirile. Aplicați stack-ul de platformă mai întâi." >&2
  exit 1
fi

(cd "${STACK_DIR}" && terraform output -raw iot_certificate_pem) > "${SECRETS_DIR}/device.pem.crt"
(cd "${STACK_DIR}" && terraform output -raw iot_private_key) > "${SECRETS_DIR}/private.pem.key"
chmod 600 "${SECRETS_DIR}/private.pem.key" "${SECRETS_DIR}/device.pem.crt"

echo "==> descarc certificatul rădăcină Amazon"
if [[ ! -f "${SECRETS_DIR}/AmazonRootCA1.pem" ]]; then
  curl -fsSL "${IOT_ROOT_CA_URL}" -o "${SECRETS_DIR}/AmazonRootCA1.pem"
fi

IOT_ENDPOINT="$(cd "${STACK_DIR}" && terraform output -raw iot_endpoint)"
IOT_THING="$(cd "${STACK_DIR}" && terraform output -raw iot_thing_name)"
# Topicul de publicare, construit ca în `cncdt_mj/telemetry.py`; filtrul din Terraform are
# wildcarduri (`cncdt/+/cnc/+/telemetry`), iar un client MQTT nu poate publica pe ele.
CELL_ID="${CNCDT_CELL_ID:-cell-a}"
cat > "${SECRETS_DIR}/iot-endpoint.env" <<EOF
# Generat de simulation/scripts/gen-iot-certs.sh
CNCDT_MQTT_HOST=${IOT_ENDPOINT}
CNCDT_MQTT_PORT=8883
CNCDT_MQTT_CLIENT_ID=${IOT_THING}
CNCDT_TELEMETRY_TOPIC=cncdt/${ENVIRONMENT}/cnc/${CELL_ID}/telemetry
CNCDT_CERT_DIR=${SECRETS_DIR}
EOF
chmod 600 "${SECRETS_DIR}/iot-endpoint.env"

echo
echo "gata:"
ls -1 "${SECRETS_DIR}"
echo
echo "Încărcați-le în mediu înainte de a rula simularea:"
echo "  set -a; source ${SECRETS_DIR#"${ROOT}/"}/iot-endpoint.env; set +a"
