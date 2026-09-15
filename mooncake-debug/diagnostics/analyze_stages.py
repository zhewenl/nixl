#!/usr/bin/env python3
"""Summarize diagnostic records, preserving parent/child scope and nesting."""
import json
from pathlib import Path
import statistics
import sys

base=Path(sys.argv[1])
case=next(base.glob('*-READ'))
manifest=json.loads((case/'initiator-manifest.json').read_text())
config=manifest['config']
submit_threads=int(manifest['environment'].get('NIXL_MC_SUBMIT_THREADS', '0'))
raw=[json.loads(line) for line in (case/'initiator-stages.jsonl').read_text().splitlines()]
warmup_parents=config['warmup_waves']*config['inflight']
rows=[row for row in raw if row['parent']>warmup_parents]
parents=[row for row in rows if row['kind']=='post']
assert len(parents)==config['measure_waves']*config['inflight']
assert all(row['descriptors']==config['descriptors'] for row in parents)
children=[row for row in rows if row['kind']=='child_submit']
if children:
    for parent in parents:
        peers=[c for c in children if c['parent']==parent['parent']]
        assert len(peers)==4
        assert sum(c['descriptors'] for c in peers)==parent['descriptors']
        assert len({c['tid'] for c in peers})==submit_threads, 'unexpected actual submit thread count'
        assert all(c['end_ns']<=parent['end_ns'] for c in peers), 'parent returned before CPU submit'
summary={}
for kind in sorted({r['kind'] for r in rows}):
    group=[r for r in rows if r['kind']==kind]
    stages={k for r in group for k in r['stages_ms']}
    summary[kind]={}
    for stage in sorted(stages):
        vals=[r['stages_ms'][stage] for r in group if stage in r['stages_ms']]
        summary[kind][stage]={'n':len(vals), 'p50_ms':statistics.median(vals), 'max_ms':max(vals)}
(base/'stage-summary.json').write_text(json.dumps(summary,indent=2))
print(json.dumps(summary,indent=2))
