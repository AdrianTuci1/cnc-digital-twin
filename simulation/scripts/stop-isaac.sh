#!/bin/bash
# Oprește și șterge containerul Isaac Sim.
set -euo pipefail

CONTAINER_NAME="${CONTAINER_NAME:-isaac-sim}"

if docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1; then
  echo "container ${CONTAINER_NAME} oprit"
else
  echo "containerul ${CONTAINER_NAME} nu rula"
fi
