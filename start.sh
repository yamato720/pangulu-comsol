#!/usr/bin/env bash
set -euo pipefail
module_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$module_root/pangu-comsol" run "$@"
