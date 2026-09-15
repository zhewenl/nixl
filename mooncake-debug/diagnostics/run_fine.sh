#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname -- "${BASH_SOURCE[0]}")/../.."
source mooncake-debug/env.sh
export LD_LIBRARY_PATH="$PWD/.local/mooncake-diag-install/lib:$LD_LIBRARY_PATH"
export NIXL_PLUGIN_DIR="$PWD/.local/sampled-plugin"
export NIXL_MC_FINE_STRIDE=1
export MC_WORKERS_PER_CTX=2 NIXL_MC_SUBMIT_THREADS=0 NIXL_MC_DIAG_ENABLE=1 NIXL_MC_FINE_TIMERS=1
python mooncake-debug/run_matrix.py --backends MOONCAKE --descriptors 4096 --mr-count 64 --warmup-waves 1 --measure-waves 2 --output mooncake-debug/runs/fine-smoke
for mode in 0 1; do
    export NIXL_MC_FINE_TIMERS="$mode"
    python mooncake-debug/run_matrix.py --backends MOONCAKE --descriptors 207360 --mr-count 256 --warmup-waves 2 --measure-waves 10 --output "mooncake-debug/runs/fine-r256-mode${mode}"
done
# Same N, byte length and gap; reduce registered regions to test scan sensitivity.
for regions in 1 64; do
    python mooncake-debug/run_matrix.py --backends MOONCAKE --descriptors 207360 --mr-count "$regions" --warmup-waves 2 --measure-waves 10 --output "mooncake-debug/runs/fine-r${regions}-mode1"
done
