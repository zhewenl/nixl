#!/usr/bin/env python3
"""Summarize only complete, validated cases, keeping raw measurements intact."""
import argparse
from collections import Counter
import json
from pathlib import Path
import statistics


def dist(values):
    values = sorted(values)
    if not values:
        return {'n': 0}
    return dict(n=len(values), p50=statistics.median(values),
                p90=values[min(len(values)-1, int((len(values)-1)*0.9))], max=max(values))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory', type=Path)
    a = p.parse_args()
    summaries = []
    for case in sorted(a.directory.glob('*')):
        if not case.is_dir():
            continue
        if not all((case / f'{r}-success.json').exists() for r in ('target', 'initiator')) or (case / 'FAILED.json').exists():
            continue
        rows = [json.loads(line) for line in (case/'initiator.jsonl').read_text().splitlines()]
        rows = [r for r in rows if r.get('warmup') is False]
        s = dict(case=case.name)
        for api in ('make_prepped_xfer', 'post', 'check', 'release', 'wave'):
            samples = [r for r in rows if r['api'] == api]
            s[api] = dist([r['wall_ms'] for r in samples])
            s[api+'_thread_cpu'] = dist([r['thread_cpu_ms'] for r in samples if 'thread_cpu_ms' in r])
            if api in ('post', 'check'):
                s[api+'_statuses'] = dict(Counter(r['status'] for r in samples))
        for status in ('DONE', 'PROC'):
            s['check_'+status] = dist([r['wall_ms'] for r in rows if r['api']=='check' and r['status']==status])
        checks = {}
        for r in rows:
            if r['api'] == 'check':
                key = (r['wave'], r['handle'])
                checks[key] = checks.get(key, 0) + r['wall_ms']
        posts = [r for r in rows if r['api']=='post']
        s['poll_total_per_handle'] = dist([checks.get((r['wave'],r['handle']),0) for r in posts])
        s['wave_process_cpu'] = dist([r['process_cpu_ms'] for r in rows if r['api']=='wave'])
        s['post_burst'] = dist([r['post_burst_ms'] for r in rows if r['api']=='wave'])
        s['payload_GB_s'] = dist([r['payload_bytes']/1e6/r['wall_ms'] for r in rows if r['api']=='wave'])
        s['telemetry_desc_counts'] = sorted({r['descCount'] for r in rows if r['api']=='telemetry'})
        summaries.append(s)
    (a.directory/'summary.json').write_text(json.dumps(summaries, indent=2))
    lines = ['# Local GPU descriptor benchmark', '',
             'Same host, two GPU processes, RDMA requested explicitly. This is not a cross-node or vLLM/TPOT measurement.',
             'P50 wall-clock milliseconds; warmup excluded. See raw JSONL, role logs and manifests for evidence.', '',
             '| Case | prep/handle | post/handle | post thread CPU | poll total/handle | wave | post DONE/PROC |',
             '|---|---:|---:|---:|---:|---:|---|']
    for s in summaries:
        lines.append('| '+s['case']+' | '+' | '.join(f'{s[k]["p50"]:.3f}' for k in
            ('make_prepped_xfer','post','post_thread_cpu','poll_total_per_handle','wave'))+' | '+str(s['post_statuses'])+' |')
    lines += ['', 'Small samples: P99 intentionally omitted. Full data/gap/guard validation and descriptor-count assertions must pass for inclusion.']
    (a.directory/'RESULTS.md').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
