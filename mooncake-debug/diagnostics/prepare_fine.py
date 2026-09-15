#!/usr/bin/env python3
"""Add exact per-request timers AFTER prepare_te.py, in the isolated TE tree."""
from pathlib import Path
import shutil
root=Path(__file__).resolve().parents[2]
helper=Path(__file__).resolve().parent
src=root/'.local/mooncake-diagnostic/mooncake-transfer-engine'
for name in ('diag.hpp','diag.cpp'): shutil.copyfile(helper/name,src/'src'/name)
def edit(path, changes):
 p=src/path;s=p.read_text()
 for old,new,count in changes:
  assert s.count(old)==count,(path,old,s.count(old))
  s=s.replace(old,new)
 p.write_text(s)
edit(Path('src/transport/rdma_transport/rdma_transport.cpp'),[
 ('    assert(local_segment_desc.get());', '    assert(local_segment_desc.get());\n    if(mcdiag::current)mcdiag::current->local_buffers=local_segment_desc->buffers.size();', 1),
 ('        const uint64_t src_rem = bytesUntilBufferEnd(', '        mcdiag::FineTimer src_time(mcdiag::SourceBoundary);\n        const uint64_t src_rem = bytesUntilBufferEnd(',1),
 ('        const uint64_t tgt_rem = bytesUntilBufferEnd(', '        src_time.stop();\n        mcdiag::FineTimer tgt_time(mcdiag::TargetBoundary);\n        const uint64_t tgt_rem = bytesUntilBufferEnd(',1),
 ('        auto request_buffer_id = -1, request_device_id = -1;', '        if(mcdiag::current && !mcdiag::current->target_buffers)mcdiag::current->target_buffers=target_segment_desc->buffers.size();\n        mcdiag::FineTimer local_select(mcdiag::LocalSelect);\n        auto request_buffer_id = -1, request_device_id = -1;',1),
 ('        SliceLengthCalculator slice_calc{', '        local_select.stop();\n        SliceLengthCalculator slice_calc{',1),
 ('            size_t slice_length = slice_calc.calculate(offset);', '            mcdiag::FineTimer calculate(mcdiag::SliceCalculate);\n            size_t slice_length = slice_calc.calculate(offset);\n            calculate.stop();\n            mcdiag::FineTimer allocate(mcdiag::SliceAllocate);',1),
 ('            slice->source_addr = (char *)request.source + offset;', '            allocate.stop();\n            mcdiag::FineTimer initialize(mcdiag::SliceInitialize);\n            slice->source_addr = (char *)request.source + offset;',1),
 ('            if (nr_slices >= kSubmitWatermark) {', '            initialize.stop();\n            if (nr_slices >= kSubmitWatermark) {',1),
 ('                                  bool hca_affinity) {', '                                  bool hca_affinity) {\n    mcdiag::FineTimer topology(mcdiag::SelectTopology);',1),
])
edit(Path('src/transport/rdma_transport/worker_pool.cpp'),[
 ('                            int &device_id, int retry_count = 0) {', '                            int &device_id, int retry_count = 0) {\n    mcdiag::FineTimer peer_select(mcdiag::PeerSelect);',1),
])
