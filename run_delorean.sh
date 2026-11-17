#!/usr/bin/env bash
# Usage: ./run_delorean.sh <file.dlr> [N]
set -euo pipefail
BASE="$(cd "$(dirname "$0")" && pwd)"
exec java -Xmx20000m -cp "$BASE/DeLorean.jar:$BASE/lib/*:$BASE/lib/HermiT/HermiT.jar" RunDeLorean "$@"