#!/usr/bin/env python3
from pathlib import Path
import shutil
root=Path(__file__).resolve().parents[2]
src=root/'.local/mooncake-diagnostic/mooncake-transfer-engine'
helper=Path(__file__).resolve().parent
for name in ('diag.hpp','diag.cpp'): shutil.copyfile(helper/name, src/'src'/name)
def edit(path, changes):
 p=src/path;s=p.read_text();s='#include "'+str(src/'src/diag.hpp')+'"\n'+s
 for old,new,count in changes:
  assert s.count(old)==count,(path,old,s.count(old))
  s=s.replace(old,new)
 p.write_text(s)
edit(Path('src/transfer_engine_c.cpp'),[
 ('    std::vector<Transport::TransferRequest> native_entries;', '    mcdiag::Timer conversion(mcdiag::CConvert);\n    std::vector<Transport::TransferRequest> native_entries;',2),
 ('    Status s =\n        native->submitTransfer', '    conversion.stop();\n    mcdiag::Timer native_time(mcdiag::TENative);\n    Status s =\n        native->submitTransfer',1),
 ('    Status s = native->submitTransferWithNotify(', '    conversion.stop();\n    mcdiag::Timer native_time(mcdiag::TENative);\n    Status s = native->submitTransferWithNotify(',1),
])
edit(Path('src/multi_transport.cpp'),[
 ('    const std::vector<TransferRequest>& entries) {', '    const std::vector<TransferRequest>& entries) {',0),
 ('    BatchID batch_id, const std::vector<TransferRequest>& entries) {',
  '    BatchID batch_id, const std::vector<TransferRequest>& entries) {\n    mcdiag::Timer prepare(mcdiag::TaskSelect);',1),
 ('    Status overall_status = Status::OK();', '    prepare.stop();\n    Status overall_status = Status::OK();',2),
])
# ENABLE_MULTI_PROTOCOL is off; remove stray prepare.stop from that separate method.
p=src/'src/multi_transport.cpp';s=p.read_text();i=s.index('Status MultiTransport::mp_submitTransfer');s=s[:i]+s[i:].replace('    prepare.stop();\n','',1);p.write_text(s)
edit(Path('src/transport/rdma_transport/rdma_transport.cpp'),[
 ('    const std::vector<TransferTask *> &task_list) {',
  '    const std::vector<TransferTask *> &task_list) {\n    mcdiag::Timer rdma(mcdiag::RdmaTotal);',1),
])
edit(Path('src/transport/rdma_transport/worker_pool.cpp'),[
 ('    const std::vector<Transport::Slice *> &slice_list) {',
  '    const std::vector<Transport::Slice *> &slice_list) {\n    mcdiag::Timer mapping(mcdiag::TargetMap);',1),
 ('    for (int shard_id = 0; shard_id < kShardCount; ++shard_id) {\n        if (slice_list_map[shard_id].empty()) continue;',
  '    mapping.stop();\n    for (int shard_id = 0; shard_id < kShardCount; ++shard_id) {\n        if (slice_list_map[shard_id].empty()) continue;\n        mcdiag::Timer lock_time(mcdiag::QueueLock);',1),
 ('        slice_queue_lock_[shard_id].lock();\n        for (auto &slice : slice_list_map[shard_id])',
  '        slice_queue_lock_[shard_id].lock();\n        lock_time.stop();\n        mcdiag::Timer enqueue_time(mcdiag::Enqueue);\n        for (auto &slice : slice_list_map[shard_id])',1),
])
# Time destruction separately, nested in the plugin's free_batch measurement.
p=src/'src/multi_transport.cpp';s=p.read_text().replace('    delete &batch_desc;','    mcdiag::Timer destroy(mcdiag::DestroyTasks);\n    delete &batch_desc;\n    destroy.stop();',1);p.write_text(s)
