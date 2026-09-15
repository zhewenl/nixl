# Mooncake: high submit and completion-query CPU cost for 200k descriptors

Tracking: [zhewenl/nixl#3](https://github.com/zhewenl/nixl/issues/3).

## Scope and versions

Measured 2026-09-14/15 UTC. NIXL **1.3.2**, checkout
`83359015be3dcb5281b46bb382ba384116e1c36b`; its runtime source equals
`4f7f1fd425976230188a303a8adb87e133b840f7` (83359015 adds a design document only).
This debug branch preserves 4f7f1fd4 as an ancestor; current fork main 1.5.0 was not
benchmarked. Mooncake TE **b5ea3f00343dfdebac40bbc1809b521a60a67eb5**, from the
private Inferact/Mooncake fork. UCX **1.20.0 / 4b7a6ca8410f9cea0e15857233ecfeefdd863dde**.

Environment: Ubuntu 24.04, kernel 6.17.0-1010-gcp-64k, aarch64, 140 ARM Neoverse-V2
CPUs across two sockets, four GB200 GPUs (189,471 MiB each); test uses GPU0 and GPU1.
Driver 580.126.09 (driver reports CUDA 13.0), build toolkit CUDA 13.1, Python 3.12.13,
PyTorch 2.14.0+cu130, GCC 13, Meson 1.12.0, Abseil 20250814.1.

Both peers use **the same HCA rocep139s0:1**, same-host RDMA loopback. No cross-node
or model/TPOT claim. DMA-BUF registration, no nvidia-peermem; 64-KiB-aligned allocations.
Mooncake release shared build: CUDA ON, HIP OFF, MULTI_PROTOCOL OFF, METRICS ON,
intra-NVLink/MNNVL/HTTP/ETCD OFF. NIXL release build with UCX/MOONCAKE and SM100.
UCX TLS `rc,cuda_copy`; Mooncake forced HCA with MC_FORCE_TCP unset,
MC_INTRA_NVLINK=0, WITH_NVIDIA_PEERMEM=0. Baseline MC_WORKERS_PER_CTX=2.
See [environment.json](environment.json) for the structured snapshot.

## Reproduction shape and observed behavior

207,360 descriptor pairs × 32,256 bytes = 6,688,604,160 payload bytes (6.229 GiB),
16 B gaps, 256 separately registered allocations per endpoint, H1, READ, notify on.
No PyTorch allocation caching, OMP_NUM_THREADS=1, two warmup and ten measured waves,
100 us polling, one process pair per case, CPUs not pinned.

P50 milliseconds, warm samples:

| Backend | post wall | post caller CPU | cumulative check | post-to-observed-DONE wave |
|---|---:|---:|---:|---:|
| Mooncake | 272.451 | 271.987 | 1000.764 | 1279.888 |
| UCX, no pool | 13.407 | 13.408 | 184.581 | 208.096 |
| UCX, four threads | 3.240 | 0.083 | 2.277 | 133.824 |

All posts returned PROC. The expected behavior is bounded submission/check overhead
appropriate for large descriptor lists; no particular latency threshold is assumed.
The symptom is hundreds of milliseconds on the caller during submit plus about one
second of completion-query work. post-to-DONE is not raw DMA or wire-transfer time.

Before 4f7f1fd4, uppercase MOONCAKE discovery did not match the library filename.
After bypassing only the name mismatch, 1024-descriptor transfers passed and 1025 /
207,360-descriptor submissions failed the old fixed batch capacity check. The fix
is necessary for this test, but does not solve the CPU-cost problem.

## Single-submit bottleneck

A later run measured post 261.252 ms with coarse timers, 267.188 ms with fine
sampling (+2.27%; run variance included). Fine measurements sample every 64th
call with an offset that varies by parent and stage. An empty-Timer calibration
(1M intervals, mean 31.3509 ns) is subtracted per estimated call. Fine values below
are approximate; nested rows and independently computed medians are not additive.

| Stage | P50 ms | Measurement |
|---|---:|---|
| Build C requests / C-to-C++ conversion | 1.223 / 1.418 | whole-stage wall |
| Task initialization + transport selection | 22.902 | whole-stage wall |
| RDMA submit total | 239.914 | whole-stage wall; includes children below |
| ↳ Local MR/device selection | ≈47.5 | sampling + empty-Timer correction |
| ↳ Slice length / MR boundary calculation | ≈83.1 | includes local ≈39.2 + remote ≈39.8 |
| ↳ Slice allocation | ≈17.7 | estimate |
| ↳ Slice initialization / task-context grouping | ≈16.8 | estimate |
| ↳ Target mapping and shard grouping | 68.128 | whole-stage wall |
| ↳↳ Remote MR/device selection | ≈47.2 | estimate inside mapping |
| ↳ Software queue lock wait / enqueue | 0.0004 / 3.242 | whole-stage wall |

TE creates a fresh SliceLengthCalculator for each descriptor, resetting its local
and remote buffer indices to zero. The cache is reused only across slices of one
request. On a miss, bytesUntilBufferEnd scans the buffer list for a valid MR's
maximum remaining range. Local selectDevice and remote selectPeerDevice also search
MR ranges and select a registered device. Consecutive requests in the same MR do
not reuse the previous request's boundary lookup. This repeated work has an N×R
component. Not all device-selection time is range scanning: nested local+remote
topology/key selection accounts for approximately 35.7 ms.

Holding N, descriptor length, gap, GPUs, HCA and software fixed:

| Regions / metadata buffers per endpoint | post wall ms | slice calculation estimate ms | post-to-DONE ms |
|---|---:|---:|---:|
| 256 / 256 | 267.188 | ≈83.1 | 1281.421 |
| 64 / 64 | 173.338 | ≈20.9 | 1189.719 |
| 1 / 1 | 134.198 | ≈3.1 | 1150.133 |

This supports repeated MR lookup/boundary work as a major bottleneck. It is a
registration-layout sensitivity test, not an implemented optimization. Metadata
buffer counts are measured; they are not a count of every HCA's underlying MR objects.

## Completion and threads are separate issues

The original terminal check contains about **942 ms of per-task status scanning**
and **62.5 ms of freeBatchID**, including about 62.1 ms task/slice destruction.
Turning notification off still leaves about 995 ms cumulative checks; reducing R
also leaves about 1 second. This rules out notification network latency alone,
not all notification-related bookkeeping. No instruction-level attribution is claimed.

Baseline submission runs on one calling thread. TE has two transfer workers per
HCA (one CQ poller, one verbs poster) plus a monitor. Increasing background workers
to four left post at about 270 ms. An isolated four-CPU-submit-thread experiment
split N into four batches and reduced post from 263.228 to 172.762 ms (~1.52×).
A serial-four-child control showed that complete-wave effects also depend on batch
partitioning; four submit threads do not guarantee faster overall completion.

The separate fully async branch moves submit AND completion polling to independent
submit/progress workers. It keeps a single batch and copies/enqueues descriptors
on post. That is a caller-latency mitigation, not elimination of the TE work.
The historical four-submit-thread results are not measurements of that async branch.

## Validation and next investigations

Full payload/gap/guard/padding checks at both peers, exact notification tags, and
NIXL descCount/totalBytes assertions passed. Each final large H1 case recorded
2,488,320 RDMA reads = N × 12 with no captured error/retry increments. NIC counters
are node-level, not per-process isolation. Logs establish RDMA, not an external-wire
path. Ten warm samples and one restart are exploratory; no reliable P99, cross-node,
AMD/NVIDIA comparison, current-main result or model-accuracy conclusion is claimed.

- Reuse validated MR lookups / consider indexes with metadata invalidation and
  overlapping-region semantics preserved.
- Reduce per-descriptor device/topology and slice-allocation work.
- Investigate the completion scan separately, including old TE batch/notify bookkeeping.
- Compare fully async caller latency, total CPU and post-to-DONE; validate lifecycle,
  failures and actual GPU consumers before treating it as a production fix.

[Reproduction commands](README.md), [diagnostic build](diagnostics/README.md),
[baseline summaries](results/main/summary.json), [fine estimates and raw scaled values](results/sampled-r256-mode1/fine-summary.json),
[region 1](results/sampled-r1-mode1/fine-summary.json), [region 64](results/sampled-r64-mode1/fine-summary.json).
Exact pinned TE reproduction requires access to the private source; it is not bundled.
