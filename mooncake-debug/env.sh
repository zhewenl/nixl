# Source this file; all changes are confined to this shell.
export NIXL_REPRO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# Override to reuse an existing matching installation instead of this checkout.
export NIXL_DEBUG_PREFIX="${NIXL_DEBUG_PREFIX:-$NIXL_REPRO_ROOT}"
export NIXL_DEBUG_CUDA_ROOT="${NIXL_DEBUG_CUDA_ROOT:-/usr/local/cuda}"
export PATH="$NIXL_DEBUG_PREFIX/.venv/bin:$NIXL_DEBUG_PREFIX/.local/ucx/bin:$NIXL_DEBUG_CUDA_ROOT/bin:$PATH"
export LD_LIBRARY_PATH="$NIXL_DEBUG_PREFIX/.venv/lib:$NIXL_DEBUG_PREFIX/.local/mooncake/lib:$NIXL_DEBUG_PREFIX/.local/mooncake/lib64:$NIXL_DEBUG_PREFIX/.local/ucx/lib:$NIXL_DEBUG_CUDA_ROOT/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export NIXL_PLUGIN_DIR="$NIXL_DEBUG_PREFIX/.venv/lib/plugins"
export PYTORCH_NO_CUDA_MEMORY_CACHING=1
export OMP_NUM_THREADS=1
