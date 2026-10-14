#!/bin/bash
# Pornește Isaac Sim cu livestreaming WebRTC.
#
# Acesta este scriptul canonic din repository. Instanța primește o copie echivalentă la
# bootstrap, iar `twinctl push` suprascrie copia de pe instanță cu această versiune.
#
#   ISAAC_IMAGE=nvcr.io/nvidia/isaac-sim:6.1.0 ./start-isaac.sh
set -euo pipefail

ISAAC_IMAGE="${ISAAC_IMAGE:-nvcr.io/nvidia/isaac-sim:5.0.0}"
ENTRYPOINT_SCRIPT="${ENTRYPOINT_SCRIPT:-./isaac-sim.streaming.sh}"
CONTAINER_NAME="${CONTAINER_NAME:-isaac-sim}"
SIGNALING_PORT="${SIGNALING_PORT:-8211}"

# Isaac Sim anunță browserului adresa la care poate fi atins. Într-un VPC, interfața are doar
# IP privat, iar IP-ul public EC2 este NAT 1:1 peste ea, deci fără adresa publică candidatul
# ICE rămâne nerutabil și fluxul video nu pornește niciodată.
PUBLIC_IP="${PUBLIC_IP:-}"
if [ -z "${PUBLIC_IP}" ]; then
  token="$(curl -sS -X PUT http://169.254.169.254/latest/api/token \
    -H 'X-aws-ec2-metadata-token-ttl-seconds: 120' 2>/dev/null || true)"
  if [ -n "${token}" ]; then
    PUBLIC_IP="$(curl -sS -H "X-aws-ec2-metadata-token: ${token}" \
      http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || true)"
  fi
fi

# Cheia de mai jos ajunge la scriptul de livestreaming din imagine. Numele ei diferă între
# versiunile Isaac Sim; cea din 5.0 este `publicEndpointAddress`. O suprascrieți prin
# ISAAC_EXTRA_ARGS dacă versiunea folosită anunță altă cheie.
ISAAC_EXTRA_ARGS="${ISAAC_EXTRA_ARGS:-}"
if [ -z "${ISAAC_EXTRA_ARGS}" ] && [ -n "${PUBLIC_IP}" ]; then
  ISAAC_EXTRA_ARGS="--/app/livestream/publicEndpointAddress=${PUBLIC_IP}"
fi

STATE_DIR="${STATE_DIR:-/opt/cncdt}"
ASSET_DIR="${ASSET_DIR:-${STATE_DIR}/assets}"
LOG_DIR="${LOG_DIR:-${STATE_DIR}/logs}"
CACHE_DIR="${CACHE_DIR:-${STATE_DIR}/cache}"
SIM_DIR="${SIM_DIR:-${STATE_DIR}/simulation}"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker lipsește. Rulați bootstrap-ul instanței sau instalați docker." >&2
  exit 1
fi

if ! docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q nvidia; then
  echo "Runtime-ul NVIDIA nu este configurat pentru docker." >&2
  echo "Rulați: sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker" >&2
  exit 1
fi

if command -v nvidia-smi >/dev/null 2>&1; then
  echo "GPU detectat: $(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)"
else
  echo "ATENȚIE: nvidia-smi lipsește. Fără drivere NVIDIA, containerul nu va porni." >&2
fi

mkdir -p "${ASSET_DIR}" "${LOG_DIR}" "${SIM_DIR}" \
  "${CACHE_DIR}"/{kit,ov,glcache,computecache}

if docker ps --format '{{.Names}}' | grep -qx "${CONTAINER_NAME}"; then
  echo "containerul ${CONTAINER_NAME} rulează deja"
  exit 0
fi

docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

# `--network host` este obligatoriu: negocierea ICE folosește mai multe porturi,
# iar maparea individuală de porturi nu acoperă intervalul de media.
docker run -d \
  --name "${CONTAINER_NAME}" \
  --gpus all \
  --network host \
  --ipc host \
  --restart unless-stopped \
  -e ACCEPT_EULA=Y \
  -e PRIVACY_CONSENT=Y \
  -e OMNI_KIT_ACCEPT_EULA=Y \
  -e "ISAAC_SIM_SIGNALING_PORT=${SIGNALING_PORT}" \
  -e "PUBLIC_IP=${PUBLIC_IP}" \
  -e "AWS_REGION=${AWS_REGION:-eu-central-1}" \
  -e "CNCDT_MQTT_HOST=${CNCDT_MQTT_HOST:-localhost}" \
  -e "CNCDT_MQTT_PORT=${CNCDT_MQTT_PORT:-8883}" \
  -e "CNCDT_TELEMETRY_TOPIC=${CNCDT_TELEMETRY_TOPIC:-cncdt/dev/cnc/cell-a/telemetry}" \
  -e "CNCDT_EXT_PATH=${CNCDT_EXT_PATH:-/sim/extensions}" \
  -v "${ASSET_DIR}:/assets:ro" \
  -v "${SIM_DIR}:/sim:ro" \
  -v "${LOG_DIR}:/logs:rw" \
  -v "${CACHE_DIR}/kit:/isaac-sim/kit/cache:rw" \
  -v "${CACHE_DIR}/ov:/root/.cache/ov:rw" \
  -v "${CACHE_DIR}/glcache:/root/.cache/nvidia/GLCache:rw" \
  -v "${CACHE_DIR}/computecache:/root/.nv/ComputeCache:rw" \
  "${ISAAC_IMAGE}" \
  bash -lc "${ENTRYPOINT_SCRIPT} ${ISAAC_EXTRA_ARGS}"

cat <<EOF

container pornit: ${CONTAINER_NAME}
  loguri:     docker logs -f ${CONTAINER_NAME}
  gata când:  în log apare "App ready" sau "... Headless WebRTC App is loaded"
  client:     http://${PUBLIC_IP:-127.0.0.1}:${SIGNALING_PORT}/streaming/webrtc-client/?server=${PUBLIC_IP:-127.0.0.1}
EOF
