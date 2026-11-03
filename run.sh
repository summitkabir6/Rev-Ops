#!/usr/bin/env bash
# One command for everything. Each step loads data (idempotently) and rebuilds the model.
#
#   ./run.sh report             weekly report from data/raw, last full week before today
#   ./run.sh report --dry-run   same, from the synthetic rows in data/sample
#   ./run.sh reconcile [--start YYYY-MM-DD --end YYYY-MM-DD] [--dry-run]
#   ./run.sh test
set -euo pipefail
cd "$(dirname "$0")"

case "${1:-report}" in
  report)    shift || true; python -m src.report.weekly "$@" ;;
  reconcile) shift; python -m src.reconcile.reconcile "$@" ;;
  test)      python -m pytest -q ;;
  sample)    python data/sample/make_sample.py ;;
  *)         echo "usage: ./run.sh [report|reconcile|test|sample] [options]" >&2; exit 2 ;;
esac
