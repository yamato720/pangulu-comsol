#!/bin/sh
set -eu
module_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
exec python3 -B "$module_root/dev/benchmarks/s/run.py" "$@"
