/* SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0 */
#include "mooncake_legacy_drain.h"
#ifdef NIXL_MOONCAKE_LEGACY_TE_BUILD_ID
#include "transport/transport.h"
#include <dlfcn.h>
#include <link.h>
#include <cstring>
#include <string>

namespace {
struct BuildIdentity {
    const void *base;
    bool matches = false;
};

int
inspect(struct dl_phdr_info *info, size_t, void *opaque) {
    auto &identity = *static_cast<BuildIdentity *>(opaque);
    if (reinterpret_cast<const void *>(info->dlpi_addr) != identity.base) {
        return 0;
    }
    for (size_t i = 0; i < info->dlpi_phnum; ++i) {
        const auto &ph = info->dlpi_phdr[i];
        if (ph.p_type != PT_NOTE) {
            continue;
        }
        const auto *p = reinterpret_cast<const unsigned char *>(info->dlpi_addr + ph.p_vaddr);
        const auto *end = p + ph.p_memsz;
        while (size_t(end - p) >= sizeof(ElfW(Nhdr))) {
            ElfW(Nhdr) note;
            std::memcpy(&note, p, sizeof(note));
            p += sizeof(note);
            size_t names = (size_t(note.n_namesz) + 3) & ~size_t(3);
            size_t descs = (size_t(note.n_descsz) + 3) & ~size_t(3);
            if (names > size_t(end - p) || descs > size_t(end - p) - names) {
                break;
            }
            if (note.n_type == NT_GNU_BUILD_ID && note.n_namesz == 4 &&
                std::memcmp(p, "GNU", 4) == 0) {
                static const char hex[] = "0123456789abcdef";
                std::string id;
                for (size_t j = 0; j < note.n_descsz; ++j) {
                    auto byte = p[names + j];
                    id += hex[byte >> 4];
                    id += hex[byte & 15];
                }
                identity.matches = id == NIXL_MOONCAKE_LEGACY_TE_BUILD_ID;
                return 1;
            }
            p += names + descs;
        }
    }
    return 1;
}
} // namespace

bool
nixlMooncakeLegacyDrainCompatible() {
    Dl_info factory{}, submit{}, query{}, free_batch{};
    if (!dladdr(reinterpret_cast<void *>(&createTransferEngine), &factory) ||
        !dladdr(reinterpret_cast<void *>(&submitTransfer), &submit) ||
        !dladdr(reinterpret_cast<void *>(&getTransferStatus), &query) ||
        !dladdr(reinterpret_cast<void *>(&freeBatchID), &free_batch) ||
        factory.dli_fbase != submit.dli_fbase || factory.dli_fbase != query.dli_fbase ||
        factory.dli_fbase != free_batch.dli_fbase) {
        return false;
    }
    BuildIdentity identity{factory.dli_fbase};
    dl_iterate_phdr(inspect, &identity);
    return identity.matches;
}

int
nixlMooncakePrepareFailedSubmitDrain(batch_id_t id) {
    // Fixed b5ea3f0 public headers, guarded by the loaded TE build identity.
    // MultiTransport initializes ALL routes before dispatching ANY transport.
    // A null route therefore identifies pre-dispatch failure. Verify the whole
    // batch has no slices before marking unstarted tasks reclaimable. Never
    // fabricate completion for a slice that could have reached a QP.
    auto &batch = mooncake::Transport::toBatchDesc(id);
    bool missing_route = false;
    for (auto &task : batch.task_list) {
        missing_route |= task.transport_ == nullptr;
    }
    if (missing_route) {
        for (auto &task : batch.task_list) {
            if (task.slice_count || !task.slice_list.empty() || task.success_slice_count ||
                task.failed_slice_count) {
                return -1;
            }
        }
        for (auto &task : batch.task_list) {
            task.is_finished = true;
        }
        return 1; // freeBatchID will confirm; do not query these null routes.
    }
    // Fully routed batches may contain active DMA. Their existing RDMA error
    // cleanup marks unposted/unstarted slices failed; ordinary task polling must
    // still drain all tasks. This function deliberately does NOT free them.
    return 0;
}
#else
bool
nixlMooncakeLegacyDrainCompatible() {
    return false;
}

int
nixlMooncakePrepareFailedSubmitDrain(batch_id_t) {
    return -1;
}
#endif
