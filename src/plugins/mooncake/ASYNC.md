# Experimental W1 asynchronous execution

This opt-in implementation moves C API allocation, submit, polling, notification
sending and batch reclamation off the caller. It uses one backend-wide submit
thread and one separate progress thread. A handle remains one TE batch: no
chunking, no new Mooncake batch query API, no Mooncake rebuild.

Default `NIXL_MOONCAKE_ASYNC=0` retains synchronous execution. `=1` requires the
explicit, version-bound legacy drain adapter described below. Other values fail
initialization. Admission limits (queued **and active** work):

| Environment variable | Default |
|---|---:|
| `NIXL_MOONCAKE_ASYNC_MAX_JOBS` | 64 |
| `NIXL_MOONCAKE_ASYNC_MAX_DESCRIPTORS` | 4194304 |
| `NIXL_MOONCAKE_ASYNC_MAX_BYTES` | 68719476736 |
| `NIXL_MOONCAKE_ASYNC_POLL_US` | 1000 |

Limits are positive integers. Queue rejection returns `NIXL_ERR_NOT_ALLOWED`
without accepting work; it is not silently queued or synchronously waited out.
A request snapshot is constructed before final admission, so these are accepted
work limits, not a hard limit on concurrent callers' temporary allocations.
Applications must serialize operations on a given handle, as required by the
existing core request state. Descriptors are copied in `postXfer`, including the
current operation, segment, addresses, lengths and original notification string.
This O(N) caller cost is included in measurements, not hidden in `prepXfer`.

Each accepted post has a distinct small completion object. Background workers
own all large arrays. The caller sees IN_PROG while queued, submitting, polling,
notifying or reclaiming; terminal state is published with release/acquire after
safe reclamation. `checkXfer` reads only this state. Repost/early release are
rejected while active. There is no cancellation of already accepted DMA.

Registration/connection guards are conservative in W1: any active work blocks
local deregistration, disconnect and replacement of an existing connection with
new identity. Core now retains descriptors on deregistration failure, honors
remote disconnect failures before erasing metadata, and preserves IN_PROG when
an attempted release is rejected (including a repeated release). The Python
invalidation binding propagates errors instead of discarding busy status.
Actual buffer allocations and remote source lifetimes remain the application's
responsibility; copying addresses does not keep an arbitrary freed tensor alive.

Shutdown closes admission, drains/join workers, then destroys TE. Terminal
completion is not exposed on TIMEOUT merely because time elapsed: old TE may
still have DMA outstanding. A permanently broken TE query/device that cannot
prove drain can retain resources; do not force-free them. Hardware disappearance
and interpreter global teardown are not certified by the current tests.

## Fixed legacy TE recovery adapter

The tested library is unmodified `b5ea3f00343dfdebac40bbc1809b521a60a67eb5`:
SHA256 `dd7970e81d14fa6574daeb4332682aad9747d186b023de485e7be77983094add`,
GNU build ID `391ac55b1cf35c347260b6f34268d8ad22b7785b`.

The C API alone cannot safely query a partially initialized batch after submit
failure: old `MultiTransport` may return before filling every task's transport,
and task querying can assert on an uninitialized task. The experimental NIXL
adapter uses **that revision's exact public Transport definitions**. Configure
`-Dmooncake_legacy_te_build_id=391ac55b1cf35c347260b6f34268d8ad22b7785b` with its
matching headers and HIP/multi-protocol/metrics build flags. This is an ABI-bound
prototype, not a portable promise for arbitrary Mooncake builds.

Before enabling async, compare the loaded library's build identity and the C
factory/submit/query/free symbol owners. Experimental harnesses also check the
actual SHA256. A matching build ID does not replace selecting the correct headers
and compile flags. There is no object-layout guess on an arbitrary loaded TE.

Old MultiTransport finishes all route selection before dispatching any transport.
If any task has a null transport, the adapter verifies **every** task has no
slices/counters, then marks only this never-dispatched batch reclaimable. On
transport-side partial submission, normal task polling drains all tasks, including
failed+pending combinations. `freeBatchID` must confirm safety before publishing
an error. There is no forced free of active slices, no fabricated successful data,
and no auto retry of a logical transfer.

## Costs and remaining semantics

The old per-task C polling algorithm is retained, including its internal repeated
whole-batch checks. Polling cost is moved, not eliminated. The async path uses
plain `submitTransfer`: NIXL retains the original notification and calls existing
`genNotifyInEngine` once, from the progress thread, after successful data drain
and safe batch reclamation. Only then is success published. A nonzero RPC return
publishes an error; it is never blindly retried, since a lost reply can mean the
notification already arrived. Failed transfers never send the original payload.
This avoids old TE's pending-notification map retaining a failed batch's entry
across BatchID reuse, and avoids its swallowed notification RPC return code.

The progress thread can still be held by a slow notification RPC; caller post
and check remain independent, but other completions may wait. Explicit `genNotif`
and incoming `getNotifs` remain their existing API paths. A separate notification
worker or a typed C++ batch-query adapter are later ablations.

Per-job logs report descriptors/bytes, queue delay, submit wall/thread CPU, poll
wall/thread CPU, poll passes and final status. Foreground prep/post/check/release
and complete serving process CPU are measured by the external harness. Fast post
alone does not establish a throughput or TPOT improvement.

Tests include deterministic ASan/UBSan delayed/failed fake TE cases, real GPU
poison/guards, same-handle reuse, MR boundaries, repeated early release,
deregister/invalidate/repost refusal, peer process exit, and cross-node READ/WRITE
with captured GPU consumers immediately after DONE/notification. See the campaign
report for exact artifacts and failures; synthetic-MTP Radix is performance-only.
