#!/usr/bin/env python3
"""Validate warm single-submit samples and estimate totals from systematic sampling.

Exact per-call timers: stride=1. With stride=64, scale each stage's observed
sum by attempted calls / sampled calls. Offset rotates by parent*17 + stage*7 to avoid
repeatedly choosing the same descriptor/MR positions. These are estimates.
"""
import json
from pathlib import Path
import statistics
import sys
base=Path(sys.argv[1])
case=next(base.glob('*-READ'))
for role in ('initiator','target'):
 assert (case/f'{role}-success.json').exists()
assert not (case/'FAILED.json').exists()
manifest=json.loads((case/'initiator-manifest.json').read_text())
cfg=manifest['config'];n=cfg['descriptors']
assert cfg['inflight']==1
rows=[json.loads(x) for x in (case/'initiator-stages.jsonl').read_text().splitlines()]
rows=[r for r in rows if r['kind']=='post' and r['parent']>cfg['warmup_waves']]
assert len(rows)==cfg['measure_waves']
assert len({r['tid'] for r in rows})==1
fine=('local_select_device','slice_calculate','source_boundary','target_boundary',
      'slice_allocate','slice_initialize_group','peer_select_device','select_topology')
scaled=[]
for row in rows:
 assert row['descriptors']==n
 values=dict(row['stages_ms'])
 for stage in fine:
  if stage not in values:continue
  attempts=row.get('fine_attempts',{}).get(stage,row['stage_calls'][stage])
  assert attempts==n*(2 if stage=='select_topology' else 1),(stage,attempts,n)
  calls=row['stage_calls'][stage]
  if row.get('fine_stride',1)==1:assert attempts==calls
  values[stage]*=attempts/calls
 if 'local_select_device' in values:
  # Subtract per record, never independently computed medians.
  values['rdma_residual_estimate']=values['rdma_submit']-sum(values[k] for k in
   ('local_select_device','slice_calculate','slice_allocate','slice_initialize_group','target_mapping_grouping','queue_lock_wait','queue_enqueue'))
  values['mapping_except_peer_estimate']=values['target_mapping_grouping']-values['peer_select_device']
  values['boundary_share_of_post_estimate']=(values['source_boundary']+values['target_boundary'])/values['parent_post']
 scaled.append(values)
summary={stage:{'p50':statistics.median(r[stage] for r in scaled),
                'mean':statistics.mean(r[stage] for r in scaled),
                'min':min(r[stage] for r in scaled),'max':max(r[stage] for r in scaled)}
         for stage in scaled[0]}
result={'n':len(rows),'fine_stride':rows[0].get('fine_stride',1),
        'method':'fine timers estimated by attempts/sample count; coarse timers exact; no clock cost subtraction',
        'stages':summary}
calibration_path=base.parent/'clock-calibration.json'
if calibration_path.exists() and any(k in scaled[0] for k in fine):
 calibration=json.loads(calibration_path.read_text())
 cost=calibration['mean_empty_interval_ns']
 corrected=[]
 for row, values in zip(rows,scaled):
  adjusted=dict(values)
  for stage in fine:
   attempts=row.get('fine_attempts',{}).get(stage,row['stage_calls'][stage])
   adjusted[stage]-=cost*attempts/1e6
  adjusted['rdma_residual_estimate']=adjusted['rdma_submit']-sum(adjusted[k] for k in
   ('local_select_device','slice_calculate','slice_allocate','slice_initialize_group','target_mapping_grouping','queue_lock_wait','queue_enqueue'))
  adjusted['mapping_except_peer_estimate']=adjusted['target_mapping_grouping']-adjusted['peer_select_device']
  adjusted['boundary_share_of_post_estimate']=(adjusted['source_boundary']+adjusted['target_boundary'])/adjusted['parent_post']
  corrected.append(adjusted)
 result['empty_timer_calibration']=calibration
 result['approx_clock_corrected_stages']={stage:{'p50':statistics.median(r[stage] for r in corrected),
      'mean':statistics.mean(r[stage] for r in corrected)} for stage in corrected[0]}
 result['clock_correction_limit']='Subtracts mean empty Timer interval per attempted call; remaining instrumentation, cache and sampling bias are not removed. Approximate, not exact accounting.'
(base/'fine-summary.json').write_text(json.dumps(result,indent=2)+'\n')
print(base.name,json.dumps({k:round(v['p50'],4) for k,v in summary.items()}))
