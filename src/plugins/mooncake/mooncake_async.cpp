/*
 * SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
#include "mooncake_async.h"
#include "mooncake_legacy_drain.h"
#include "common/nixl_log.h"
#include <cstdlib>
#include <stdexcept>
#include <time.h>

namespace {
uint64_t
nowNs(clockid_t clock = CLOCK_MONOTONIC) {
    timespec ts{};
    clock_gettime(clock, &ts);
    return uint64_t(ts.tv_sec) * 1000000000ULL + ts.tv_nsec;
}

size_t
limit(const char *key, size_t fallback) {
    const char *value = std::getenv(key);
    if (!value) {
        return fallback;
    }
    if (!*value || *value == '-') {
        throw std::invalid_argument(key);
    }
    char *end = nullptr;
    errno = 0;
    unsigned long long n = std::strtoull(value, &end, 10);
    if (errno || *end || !n || n > SIZE_MAX) {
        throw std::invalid_argument(key);
    }
    return size_t(n);
}
} // namespace

nixlMooncakeAsync::nixlMooncakeAsync(transfer_engine_t engine)
    : engine_(engine),
      max_jobs_(limit("NIXL_MOONCAKE_ASYNC_MAX_JOBS", 64)),
      max_descriptors_(limit("NIXL_MOONCAKE_ASYNC_MAX_DESCRIPTORS", 4194304)),
      max_bytes_(limit("NIXL_MOONCAKE_ASYNC_MAX_BYTES", 64ULL << 30)),
      poll_interval_(limit("NIXL_MOONCAKE_ASYNC_POLL_US", 1000)) {
    if (!nixlMooncakeLegacyDrainCompatible()) {
        throw std::runtime_error("MOONCAKE async requires a verified legacy-TE drain adapter");
    }
    // Start progress first, with exception-safe cleanup if the second thread fails.
    progress_thread_ = std::thread(&nixlMooncakeAsync::progressLoop, this);
    try {
        submit_thread_ = std::thread(&nixlMooncakeAsync::submitLoop, this);
    }
    catch (...) {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            submit_stopped_ = true;
        }
        wake_progress_.notify_one();
        progress_thread_.join();
        throw;
    }
}

nixlMooncakeAsync::~nixlMooncakeAsync() {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        stopping_ = true;
    }
    wake_submit_.notify_one();
    submit_thread_.join();
    wake_progress_.notify_one();
    progress_thread_.join();
}

nixl_status_t
nixlMooncakeAsync::enqueue(std::unique_ptr<Job> &job) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (stopping_ || outstanding_.load() >= max_jobs_ ||
        job->requests.size() > max_descriptors_ - descriptors_ ||
        job->bytes > max_bytes_ - bytes_) {
        return NIXL_ERR_NOT_ALLOWED;
    }
    // deque allocation can fail: update accounting only after ownership transfers.
    job->queued_ns = nowNs();
    queued_.push_back(std::move(job));
    descriptors_ += queued_.back()->requests.size();
    bytes_ += queued_.back()->bytes;
    outstanding_.fetch_add(1, std::memory_order_release);
    wake_submit_.notify_one();
    return NIXL_IN_PROG;
}

void
nixlMooncakeAsync::submitLoop() {
    for (;;) {
        std::unique_ptr<Job> job;
        {
            std::unique_lock<std::mutex> lock(mutex_);
            wake_submit_.wait(lock, [this] { return stopping_ || !queued_.empty(); });
            if (queued_.empty()) {
                break;
            }
            job = std::move(queued_.front());
            queued_.pop_front();
        }
        job->submit_start_ns = nowNs();
        auto cpu = nowNs(CLOCK_THREAD_CPUTIME_ID);
        if (!job->requests.empty()) {
            job->batch = allocateBatchID(engine_, job->requests.size());
            if (static_cast<int64_t>(job->batch) < 0) {
                // The fixed legacy TE also returns negative error codes in BatchID.
                job->batch = INVALID_BATCH;
                job->failed = true;
            } else {
                // NIXL owns notifications. Old TE can retain its notification
                // map entry after a failed batch is freed and reuse that ID.
                job->submit_result =
                    submitTransfer(engine_, job->batch, job->requests.data(), job->requests.size());
            }
        }
        job->failed |= job->submit_result != 0;
        job->submit_ns = nowNs() - job->submit_start_ns;
        job->submit_cpu_ns = nowNs(CLOCK_THREAD_CPUTIME_ID) - cpu;
        if (job->submit_result) {
            NIXL_ERROR << "MOONCAKE async submit failed rc=" << job->submit_result
                       << "; retaining batch until safe drain";
        }
        {
            std::lock_guard<std::mutex> lock(mutex_);
            submitted_.push_back(std::move(job));
        }
        wake_progress_.notify_one();
    }
    {
        std::lock_guard<std::mutex> lock(mutex_);
        submit_stopped_ = true;
    }
    wake_progress_.notify_one();
}

bool
nixlMooncakeAsync::progress(Job &job) {
    if (job.batch == INVALID_BATCH) {
        return true;
    }
    // Submit errors can occur before route initialization or after partial
    // RDMA dispatch. The version-verified adapter distinguishes them without
    // freeing any task whose DMA is still active.
    if (job.submit_result) {
        if (job.error_drain_mode == -2) {
            job.error_drain_mode = nixlMooncakePrepareFailedSubmitDrain(job.batch);
        }
        if (job.error_drain_mode < 0) {
            return false;
        }
        if (job.error_drain_mode == 1) {
            return freeBatchID(engine_, job.batch) == 0;
        }
    }
    // Keep the old polling algorithm for the W1 isolation experiment. All
    // tasks are revisited on each pass, and FAILED does not skip pending tasks.
    job.cursor = 0;
    while (job.cursor < job.requests.size()) {
        transfer_status_t status{};
        int rc = getTransferStatus(engine_, job.batch, job.cursor, &status);
        if (rc) {
            job.failed = true;
            return false;
        }
        if (status.status == STATUS_PENDING || status.status == STATUS_WAITING) {
            return false;
        }
        if (status.status == STATUS_TIMEOUT) {
            job.failed = true;
            return false;
        }
        if (status.status != STATUS_COMPLETED) {
            job.failed = true;
        }
        ++job.cursor;
    }
    return freeBatchID(engine_, job.batch) == 0;
}

void
nixlMooncakeAsync::finish(std::unique_ptr<Job> job) {
    const size_t n = job->requests.size(), bytes = job->bytes;
    auto completion = job->completion;
    // Data has drained and the batch is safely freed. Send the original
    // notification once, off the caller and without any TE-owned batch entry.
    // A lost reply may mean delivered: never blindly retry this RPC.
    if (!job->failed && job->has_notification) {
        notify_msg_t msg{const_cast<char *>(job->sender.c_str()),
                         const_cast<char *>(job->notification.c_str())};
        auto start = nowNs();
        job->notify_result = genNotifyInEngine(engine_, job->segment_id, msg);
        job->notify_ns = nowNs() - start;
        job->failed = job->notify_result != 0;
        if (job->failed) {
            NIXL_ERROR << "MOONCAKE async notification failed rc=" << job->notify_result;
        }
    }
    const auto result = job->failed ? NIXL_ERR_BACKEND : NIXL_SUCCESS;
    NIXL_INFO << "MOONCAKE async completed n=" << n << " bytes=" << bytes
              << " queue_ns=" << job->submit_start_ns - job->queued_ns
              << " submit_ns=" << job->submit_ns << " submit_cpu_ns=" << job->submit_cpu_ns
              << " poll_ns=" << job->poll_ns << " poll_cpu_ns=" << job->poll_cpu_ns
              << " notify_ns=" << job->notify_ns << " notify_rc=" << job->notify_result
              << " polls=" << job->polls << " status=" << result;
    // Free large storage off the calling thread, before publishing completion.
    job.reset();
    {
        std::lock_guard<std::mutex> lock(mutex_);
        descriptors_ -= n;
        bytes_ -= bytes;
        outstanding_.fetch_sub(1, std::memory_order_release);
    }
    completion->status.store(result, std::memory_order_release);
}

void
nixlMooncakeAsync::progressLoop() {
    std::deque<std::unique_ptr<Job>> active;
    for (;;) {
        {
            std::unique_lock<std::mutex> lock(mutex_);
            if (active.empty()) {
                wake_progress_.wait(lock,
                                    [this] { return submit_stopped_ || !submitted_.empty(); });
            }
            while (!submitted_.empty()) {
                active.push_back(std::move(submitted_.front()));
                submitted_.pop_front();
            }
            if (submit_stopped_ && active.empty()) {
                return;
            }
        }
        for (auto it = active.begin(); it != active.end();) {
            auto wall = nowNs(), cpu = nowNs(CLOCK_THREAD_CPUTIME_ID);
            bool done = progress(**it);
            (*it)->poll_ns += nowNs() - wall;
            (*it)->poll_cpu_ns += nowNs(CLOCK_THREAD_CPUTIME_ID) - cpu;
            ++(*it)->polls;
            if (done) {
                finish(std::move(*it));
                it = active.erase(it);
            } else {
                ++it;
            }
        }
        if (!active.empty()) {
            std::unique_lock<std::mutex> lock(mutex_);
            wake_progress_.wait_for(lock, poll_interval_, [this] { return !submitted_.empty(); });
        }
    }
}
