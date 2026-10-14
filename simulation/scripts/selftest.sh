#!/bin/bash
# Raport scurt despre starea mediului de simulare, folosit de `twinctl selftest`.
#
# Nu folosește `set -e`: vrem să raportăm toate verificările, chiar dacă unele eșuează.
set -uo pipefail

CONTAINER_NAME="${CONTAINER_NAME:-isaac-sim}"
STATE_DIR="${STATE_DIR:-/opt/cncdt}"

section() { printf '\n=== %s ===\n' "$1"; }

section "Sistem"
printf 'kernel:   %s\n' "$(uname -sr)"
printf 'uptime:   %s\n' "$(uptime -p 2>/dev/null || uptime)"
printf 'memorie:  %s\n' "$(free -h 2>/dev/null | awk '/^Mem:/ {print $3 " / " $2}' || echo 'n/a')"

section "GPU"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,driver_version,memory.total,memory.used,utilization.gpu,temperature.gpu \
    --format=csv,noheader
else
  echo "nvidia-smi lipsește: driverele NVIDIA nu sunt instalate"
fi

section "Docker"
docker --version 2>/dev/null || echo "docker lipsește"
runtimes="$(docker info --format '{{json .Runtimes}}' 2>/dev/null || echo '')"
if grep -q nvidia <<<"${runtimes}"; then
  echo "runtime nvidia: configurat"
else
  echo "runtime nvidia: LIPSEȘTE (rulați nvidia-ctk runtime configure --runtime=docker)"
fi

section "Container Isaac Sim"
if docker ps --filter "name=${CONTAINER_NAME}" --format '{{.Names}} {{.Status}}' 2>/dev/null | grep -q .; then
  docker ps --filter "name=${CONTAINER_NAME}" --format '{{.Names}} {{.Status}}'
  echo "-- ultimele linii din log --"
  docker logs --tail 20 "${CONTAINER_NAME}" 2>&1 | tail -20
else
  echo "containerul ${CONTAINER_NAME} nu rulează"
  echo "pornire: ${STATE_DIR}/scripts/start-isaac.sh"
fi

section "Porturi de streaming"
for port in 8211 49100 3478; do
  if ss -lntu 2>/dev/null | grep -q ":${port} "; then
    echo "  ${port}: în ascultare"
  else
    echo "  ${port}: închis"
  fi
done

section "Disc"
df -h "${STATE_DIR}" 2>/dev/null | tail -1 || df -h / | tail -1

section "Sincronizare date"
if [[ -d "${STATE_DIR}/simulation" ]]; then
  echo "simulation/: prezent ($(find "${STATE_DIR}/simulation" -type f | wc -l | tr -d ' ') fișiere)"
else
  echo "simulation/: absent (rulați twinctl push)"
fi
if [[ -d "${STATE_DIR}/assets" ]]; then
  echo "assets/:     $(find "${STATE_DIR}/assets" -type f | wc -l | tr -d ' ') fișiere"
fi

section "Identitate AWS"
if command -v aws >/dev/null 2>&1; then
  aws sts get-caller-identity --query 'Arn' --output text 2>/dev/null \
    || echo "fără credențiale (rularea pe instanță le ia din rolul IAM)"
else
  echo "aws cli lipsește"
fi

section "Bootstrap"
if [[ -f "${STATE_DIR}/.bootstrapped" ]]; then
  echo "finalizat la $(date -r "${STATE_DIR}/.bootstrapped" 2>/dev/null || echo '?')"
else
  echo "NU este finalizat; verificați /var/log/cncdt-bootstrap.log"
fi
