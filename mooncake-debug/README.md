# Mooncake large-descriptor debug package

**This is the fully async experimental branch. Read [ASYNC_PORT.md](ASYNC_PORT.md)
for provenance, opt-in behavior and the AMD/HIP adapter constraint. The historical
results below describe the synchronous baseline, not an async GPU qualification.**

This branch pins the **tested NIXL 1.3.2 baseline**, not this fork's current 1.5.0
`main`. It contains commit `4f7f1fd425976230188a303a8adb87e133b840f7` unchanged:
canonical shared/static library names, conditional CUDA build dependency, and
batch capacity based on the submitted descriptor count. These prerequisite fixes
do not resolve the large-batch CPU cost tracked in [PROBLEM.md](PROBLEM.md).

- [Problem, timings, environment and limitations](PROBLEM.md)
- [Machine-readable tested environment](environment.json)
- [Installation/build layout](SETUP.md)
- [Optional fine-grained diagnostic build](diagnostics/README.md)
- [Recorded aggregate results](results/)

Related prerequisite fixes are also under review as [#1](https://github.com/zhewenl/nixl/pull/1)
and [#2](https://github.com/zhewenl/nixl/pull/2). The separate
[`zhewen/nixl-mooncake-async`](https://github.com/zhewenl/nixl/tree/zhewen/nixl-mooncake-async)
branch adds the original opt-in fully asynchronous experiment on this same baseline.
Its caller-latency behavior is different from adding four submit threads.

## Dependencies and activation

The measured Mooncake revision is in a **private fork**:
`Inferact/Mooncake@b5ea3f00343dfdebac40bbc1809b521a60a67eb5`. Exact reproduction
requires access to that source/library. No Mooncake binary or full source checkout
is bundled here. A public Mooncake release/wheel is not asserted to be equivalent.
This harness can run against another compatible installation, but record its
actual version and treat it as a different experiment.

Install the matching NIXL Python/native libraries, Mooncake TE and CUDA-capable
UCX using [SETUP.md](SETUP.md), or reuse an existing matching installation:

```bash
# Optional: directory containing .venv and .local/{mooncake,ucx}.
# export NIXL_DEBUG_PREFIX=/absolute/path/to/existing-install
source mooncake-debug/env.sh
python -c 'from nixl._api import nixl_agent, nixl_agent_config; print(nixl_agent("probe", nixl_agent_config(backends=[])).plugin_list)'
```

The launcher uses two real GPUs and one reachable HCA. Defaults reproduce the
original GPU0/GPU1 and `rocep139s0:1` setup; pass `--initiator-hca`, `--target-hca`,
`--initiator-device`, and `--target-device` for your machine. Both peers must have
a usable RDMA path. No host routing changes are made.

## Reproduce

Each output directory must be new. Runtime output is ignored under `runs/`.

```bash
python mooncake-debug/run_matrix.py --descriptors 207360 --inflight 1 4 \
  --output mooncake-debug/runs/budget --dry-run

# Small READ validation across Mooncake / UCX0 / UCX4.
python mooncake-debug/run_matrix.py --descriptors 4096 --mr-count 64 \
  --warmup-waves 1 --measure-waves 2 --output mooncake-debug/runs/smoke

# Historical shape: 207,360 pairs, 6.229 GiB payload per handle.
python mooncake-debug/run_matrix.py --descriptors 207360 --mr-count 256 \
  --inflight 1 --warmup-waves 2 --measure-waves 10 \
  --output mooncake-debug/runs/main
python mooncake-debug/analyze.py mooncake-debug/runs/main
bash mooncake-debug/collect_env.sh mooncake-debug/runs/main

# Exactly 200,000 descriptors is also supported.
python mooncake-debug/run_matrix.py --backends MOONCAKE --descriptors 200000 \
  --mr-count 256 --output mooncake-debug/runs/exact-200k

# Region sensitivity, holding N and payload fixed.
for regions in 1 64 256; do
  python mooncake-debug/run_matrix.py --backends MOONCAKE --descriptors 207360 \
    --mr-count "$regions" --output "mooncake-debug/runs/regions-$regions"
done
```

`--operation WRITE`, `--notify off`, and `--inflight 1 4` select additional cases.
The baseline Python `num_threads` option does not configure Mooncake submit
threads. MC_WORKERS_PER_CTX configures TE's background workers, a different control.
Disable the async experiment and fine timers when comparing the synchronous baseline.

## Measurement contract

- N counts local/remote descriptor pairs. Each has 32,256 payload bytes and a 16 B
  gap on both sides. NIXL merging stays enabled; telemetry must still report N.
- R independently allocated CUDA buffers are registered at each peer. H concurrent
  handles occupy disjoint slots inside those same R buffers, not R×H registrations.
- Allocation lengths are rounded to 64 KiB for the tested DMA-BUF export path.
  Every payload byte, gap, guard and padding byte is validated at both peers.
- All handles are prepared before a wave, then posted serially and polled round-robin
  every 100 us. Each wave checks notification tags exactly once per handle.
- Measures wall and calling-thread CPU time for each API. Wave spans first post
  entry to all handles observed DONE; setup, buffer initialization, validation and
  final control ACK are excluded. Wave is not a measurement of DMA duration alone.
- UCX uses `rc,cuda_copy`; Mooncake is forced to the chosen HCA with no intra-NVLink.
  The launcher unsets MC_FORCE_TCP (even `0` enables TCP in the tested revision).
  Logs must show RDMA before a run is accepted.
- Captures NIC before/after counters and loaded library paths/SHA256. Those captures
  can contain machine identifiers; review them before sharing. Only aggregate,
  machine-identifier-free historical summaries are checked into this folder.
- Both peers are terminated on failure; active allocations are not reused. Failed
  cases are excluded from summaries. Ten warm waves are exploratory, not reliable P99.

## Offline verification

```bash
python -m unittest discover -s mooncake-debug -p test_layout.py
python -m compileall -q mooncake-debug
```

`bench_two_peers.py` contains the real transfer and validation code;
`run_matrix.py` orchestrates peer processes; `analyze.py` summarizes only completed,
validated cases. The source machine's original raw logs remain local; checked-in
JSON summaries are historical evidence, not newly measured results on current main.

## Publication verification (2026-09-15)

The relocated scripts passed their three offline layout tests and a real GPU
READ smoke (4,096 descriptors, 64 regions, 1 warmup + 2 measured waves) for
MOONCAKE, UCX0 and UCX4, with full data/guard and notification checks. The smoke
reused the already-built matching baseline via NIXL_DEBUG_PREFIX; no new 200k
performance claim is made from that packaging check.
