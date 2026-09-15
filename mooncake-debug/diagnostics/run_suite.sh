#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname -- "${BASH_SOURCE[0]}")/../.."
source mooncake-debug/env.sh
run_case() {
    python mooncake-debug/run_matrix.py --backends MOONCAKE "$@"
}
# Original plugin/TE: only increase RDMA workers per HCA.
export MC_WORKERS_PER_CTX=4
run_case --descriptors 207360 --mr-count 256 --warmup-waves 2 --measure-waves 10 --output mooncake-debug/runs/background4
export MC_WORKERS_PER_CTX=2
export LD_LIBRARY_PATH="$PWD/.local/mooncake-diag-install/lib:$LD_LIBRARY_PATH"
export NIXL_PLUGIN_DIR="$PWD/.local/sampled-plugin"
export NIXL_MC_FINE_TIMERS=0
export NIXL_MC_SUBMIT_THREADS=4
export NIXL_MC_DIAG_ENABLE=1
run_case --descriptors 4096 --mr-count 64 --inflight 1 4 --warmup-waves 1 --measure-waves 2 --output mooncake-debug/runs/pool4-smoke-read
run_case --descriptors 4096 --mr-count 64 --inflight 1 4 --operation WRITE --warmup-waves 1 --measure-waves 2 --output mooncake-debug/runs/pool4-smoke-write
# Same diagnostic binaries with timers off/on; one whole batch vs four child batches.
for threads in 0 4; do
    export NIXL_MC_SUBMIT_THREADS="$threads"
    for timing in 0 1; do
        export NIXL_MC_DIAG_ENABLE="$timing"
        run_case --descriptors 207360 --mr-count 256 --warmup-waves 2 --measure-waves 10 --output "mooncake-debug/runs/submit${threads}-timers${timing}"
    done
done
