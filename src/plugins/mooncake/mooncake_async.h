/*
 * SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
#ifndef NIXL_MOONCAKE_ASYNC_H
#define NIXL_MOONCAKE_ASYNC_H

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <deque>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>
#include "nixl.h"
#include "transfer_engine_c.h"

// Only this small completion object is shared with the caller. The worker owns
// and destroys the large descriptor vector before publishing a terminal state.
struct nixlMooncakeCompletion {
    std::atomic<nixl_status_t> status{NIXL_IN_PROG};
};

class nixlMooncakeAsync {
public:
    struct Job {
        std::shared_ptr<nixlMooncakeCompletion> completion;
        std::vector<transfer_request_t> requests;
        std::string sender, notification;
        bool has_notification = false;
        size_t bytes = 0;
        batch_id_t batch = INVALID_BATCH;
        int submit_result = 0;
        int error_drain_mode = -2;
        bool failed = false;
        size_t cursor = 0;
        uint64_t queued_ns = 0, submit_start_ns = 0, submit_ns = 0;
        uint64_t submit_cpu_ns = 0, poll_ns = 0, poll_cpu_ns = 0, polls = 0;
    };

    explicit nixlMooncakeAsync(transfer_engine_t engine);
    ~nixlMooncakeAsync();

    bool
    busy() const {
        return outstanding_.load(std::memory_order_acquire) != 0;
    }

    // Consumes only on success. No TE calls or waits in this method.
    nixl_status_t
    enqueue(std::unique_ptr<Job> &job);

private:
    void
    submitLoop();
    void
    progressLoop();
    bool
    progress(Job &job);
    void
    finish(std::unique_ptr<Job> job);
    transfer_engine_t engine_;
    const size_t max_jobs_, max_descriptors_, max_bytes_;
    const std::chrono::microseconds poll_interval_;
    std::atomic<size_t> outstanding_{0};
    std::mutex mutex_;
    std::condition_variable wake_submit_, wake_progress_;
    std::deque<std::unique_ptr<Job>> queued_, submitted_;
    size_t descriptors_ = 0, bytes_ = 0;
    bool stopping_ = false, submit_stopped_ = false;
    std::thread submit_thread_, progress_thread_;
};
#endif
