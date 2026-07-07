#!/usr/bin/env bash
set -euo pipefail

mode="${1:-staged}"

if ! command -v gitleaks >/dev/null 2>&1; then
  echo "gitleaks is required to run secret scanning" >&2
  exit 1
fi

case "${mode}" in
  staged)
    git diff --cached --binary | gitleaks stdin --no-banner --redact=20
    ;;
  repo)
    gitleaks git --no-banner --redact=20 .
    ;;
  *)
    echo "usage: $0 [staged|repo]" >&2
    exit 2
    ;;
esac