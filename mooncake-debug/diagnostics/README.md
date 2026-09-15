# Optional isolated TE timers and submit-thread experiment

The main harness runs without modifying TE. These tools reproduce the historical
stage analysis using a separate build of the pinned b5ea3f00 source. Keep the
baseline `.local/mooncake` installation and the baseline NIXL plugin unchanged.
This is diagnostic code, not a production pool implementation.

Starting with the baseline layout in [SETUP.md](../SETUP.md), use a fresh detached
TE tree (the prepare scripts assert the expected source and are not idempotent):

```bash
git -C .local/src/Mooncake worktree add --detach ../../mooncake-diagnostic b5ea3f00343dfdebac40bbc1809b521a60a67eb5
python mooncake-debug/diagnostics/prepare_te.py
python mooncake-debug/diagnostics/prepare_fine.py
python mooncake-debug/diagnostics/prepare_plugin.py
```

Configure the diagnostic TE using the same CMake options and dependencies as the
baseline, but use source `.local/mooncake-diagnostic/mooncake-transfer-engine`,
build directory `.local/mooncake-diagnostic/build`, and install prefix
`.local/mooncake-diag-install`. Rebuild/install it, then compile the matching plugin:

```bash
export LIBRARY_PATH="$PWD/.local/mooncake-diag-install/lib${LIBRARY_PATH:+:$LIBRARY_PATH}"
export LD_LIBRARY_PATH="$PWD/.local/mooncake-diag-install/lib:$LD_LIBRARY_PATH"
python mooncake-debug/diagnostics/build_plugin.py .local/diagnostic-plugin-src \
  .local/sampled-plugin/libplugin_MOONCAKE.so
```

The builder reuses compile/link commands from the matching baseline
`.local/nixl-build`. Rebuild BOTH TE and plugin when diag.hpp changes: they share
an internal Context layout. The `.patch` files are readable review diffs; use the
Python prepare scripts to generate the sources at your actual checkout location.

The supplied run scripts use fresh output directories beneath `mooncake-debug/runs/`
to preserve checked-in summaries. The launcher refuses to overwrite an existing case.
`run_sampled.sh` selects the matching diagnostic library pair and runs smoke,
sampling-off/on and R1/R64 sensitivity controls. `run_suite.sh` demonstrates the
background-worker and four-submit-thread controls. `run_fine.sh` shows the earlier
high-overhead, every-call timer experiment, not the recommended performance mode.

Timer controls:

- NIXL_MC_DIAG_ENABLE=1: launcher creates per-peer stage output files.
- NIXL_MC_FINE_TIMERS=1 and NIXL_MC_FINE_STRIDE=64: sample fine stages every 64 calls,
  offset by `parent*17 + stage*7`. Stride 1 times every call and perturbs latency.
- NIXL_MC_SUBMIT_THREADS=0: original whole batch, calling-thread submit.
- NIXL_MC_SUBMIT_THREADS=4: split into four child batches and submit on four workers.
- NIXL_MC_SUBMIT_THREADS=1: the same four batches on one worker (partitioning control).

The experimental pool waits for every child CPU submit before returning. Completion
checks remain on the caller and the parent sends one notification after every child
is complete. It differs from TE's native whole-batch submit-with-notify implementation.
Fresh handles only: repost/abort/error recovery and production shutdown are not
qualified. On errors the outer harness kills both peers without reusing allocations.

`analyze_stages.py` preserves parent/child scopes and verifies actual worker TIDs
and the submit barrier. `analyze_fine.py` validates counts, scales sampled times by
attempts/samples, and optionally reads `clock-calibration.json` from the parent
results directory. It retains raw estimates plus approximate empty-Timer corrections.
Build `calibrate_clock.cpp` with `g++ -O3 -std=c++17` to obtain a new calibration.
Correction does not remove cache, sampling or branch bias; nested stages and
independent P50s cannot be added into exact accounting. See [PROBLEM.md](../PROBLEM.md).
