#!/usr/bin/env bash
# Run fuzzDL reasoner with absolute paths.
# Usage:
#   ./run_fdl.sh fdl   <input.txt>           # run fuzzDL parser/reasoner on a fuzzy DL file

set -euo pipefail

BASE="$(cd "$(dirname "$0")" && pwd)"
FDL_DIR="$BASE"

FDL_JAR="$FDL_DIR/fuzzydl.jar"
FDL_LIB="$FDL_DIR/lib"

# Gurobi setup (fuzzdl MILPHelper hardcodes Gurobi solver)
if [[ -z "${GUROBI_HOME:-}" ]]; then
    for cand in /Library/gurobi*/macos_universal2 /opt/gurobi*/macos_universal2; do
        [[ -d "$cand/lib" ]] && GUROBI_HOME="$cand" && break
    done
fi
if [[ -n "${GUROBI_HOME:-}" ]]; then
    export GUROBI_HOME
    GUROBI_LIB="$GUROBI_HOME/lib"
else
    echo "Warning: GUROBI_HOME not found. fdl mode will fail." >&2
    GUROBI_LIB=""
fi
: "${GRB_LICENSE_FILE:=$HOME/gurobi.lic}"
export GRB_LICENSE_FILE

check_jar() {
    if [[ ! -f "$1" ]]; then
        echo "Error: $1 not found. Run build.sh first." >&2
        exit 1
    fi
}

run_fdl() {
    local input="$1"
    check_jar "$FDL_JAR"
    if [[ ! -f "$input" ]]; then
        echo "Error: input file '$input' not found." >&2
        exit 1
    fi
    # CONFIG required in cwd by fuzzydl.parser.Parser
    cd "$FDL_DIR"
    if [[ ! -f CONFIG ]]; then
        echo "showVersion = false" > CONFIG
    fi
    java -Xms20000m -Xmx20000m -Djava.library.path="$GUROBI_LIB" -cp "$FDL_JAR:$FDL_LIB/*" fuzzydl.parser.Parser "$input"
}


if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <input.txt>" >&2
    exit 1
fi
run_fdl "$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"