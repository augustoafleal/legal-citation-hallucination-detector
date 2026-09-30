#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 3 ]]; then
  echo "Uso: bash run.sh <caminho_db> <pasta_txt> <arquivo_saida>" >&2
  exit 2
fi

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec env PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" python "$ROOT/scripts/generate_submission.py" "$@"
