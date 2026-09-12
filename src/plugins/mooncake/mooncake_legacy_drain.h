/* SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include "transfer_engine_c.h"
bool
nixlMooncakeLegacyDrainCompatible();
int
nixlMooncakePrepareFailedSubmitDrain(batch_id_t batch);
