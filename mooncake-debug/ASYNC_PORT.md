# Fully asynchronous branch provenance and qualification

This branch preserves the source head of
[Inferact/nixl-internal PR #2](https://github.com/Inferact/nixl-internal/pull/2)
unchanged, followed by the shared debug package and this note. The original three
commits are retained as ancestors, not reimplemented:

1. `f1055a34d079097c45f13a73e0d0e7a8fdc40840`: core ownership on rejected cleanup.
2. `4052526c8884a28919441aab9837ec954bf4bd95`: bounded W1 submit/progress workers.
3. `d1f3bf19a8aee198d0ef36460e2987af8b60d689`: notification ownership through drain.

Their common baseline is `4f7f1fd425976230188a303a8adb87e133b840f7` (NIXL 1.3.2),
also used by `zhewen/nixl-mooncake-debug`. This is not a port onto current 1.5.0 main.
All `src/`, `test/` and Meson files match the PR #2 head exactly.

## Behavior

`NIXL_MOONCAKE_ASYNC=1` enables the opt-in path; default is off. postXfer still
copies O(N) descriptors, but enqueues the work without waiting for the CPU submit.
One submit worker performs allocation/submission, and a separate progress worker
polls, sends one notification after safe drain, and reclaims the batch. checkXfer
reads an atomic completion generation. The batch is not split into four children.
Queued plus active admission is bounded. Core/Python cleanup changes are included
because queued and active work must retain registration, metadata and handle ownership.

See [the original ASYNC.md](../src/plugins/mooncake/ASYNC.md) for exact limits,
error behavior, shutdown constraints and the fixed legacy drain adapter.

## Build compatibility

The original PR's tested TE is an AMD/HIP build of b5ea3f00 with GNU build ID
`391ac55b1cf35c347260b6f34268d8ad22b7785b`. The Meson adapter configuration explicitly
uses HIP, multi-protocol and metrics layout flags when a build ID is supplied.
This branch deliberately preserves those assumptions. It is **not qualified for
the CUDA TE build used in this folder's GB200 baseline measurements**. Do not just
substitute its build ID: matching headers and object-layout compile flags are also
required. An unconfigured/incompatible adapter refuses async initialization.

The source PR remains experimental; original serving/model-correctness questions
are not resolved by publishing this branch. No GPU async performance or accuracy
claim is made here, and a lower caller post time is not proof of lower total CPU
work or DMA completion latency.

## Verification when packaging (2026-09-15)

- Source tree comparison against d1f3bf19 passed for src/, test/ and Meson files.
- The actual async_runtime_test.cpp + mooncake_async.cpp were compiled with GCC,
  AddressSanitizer and UndefinedBehaviorSanitizer and passed on this host. The test
  supplies deterministic fake TE calls: queue bounds, independent progress, fresh
  generations, partial/pre-dispatch failures, query errors, timeout drain, shutdown,
  notification failures/lost replies/no retries, allocation failure and ABI rejection.
  This is a CPU lifecycle test, not validation of the real legacy adapter or GPU DMA.
- The shared relocated harness passed real GPU MOONCAKE/UCX0/UCX4 smoke tests using
  the **synchronous baseline installation**. Checked-in 200k summaries also describe
  that historical baseline/diagnostic campaign, not this async implementation.

For the native Meson CPU test, build with `-Dbuild_tests=true` and run
`meson test -C BUILD_DIR mooncake_async_lifecycle --print-errorlogs` with matching
TE headers/build dependencies. Source PR history describes additional tests on its
original AMD environment; they were not rerun as part of this publication.
