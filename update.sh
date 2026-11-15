#!/usr/bin/env bash
set -euo pipefail
module_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 -B "$module_root/pc.py" install-sdk "$@"
