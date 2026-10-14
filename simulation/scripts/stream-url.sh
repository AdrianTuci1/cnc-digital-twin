#!/bin/bash
# Afișează URL-ul clientului WebRTC, așa cum trebuie deschis în browser.
#
#   ./stream-url.sh              # prin tunel local
#   ./stream-url.sh 10.0.0.5     # direct, când TURN/STUN este configurat
set -euo pipefail

HOST="${1:-127.0.0.1}"
PORT="${ISAAC_SIGNALING_PORT:-8211}"

echo "http://${HOST}:${PORT}/streaming/webrtc-client/?server=${HOST}&port=${PORT}"
