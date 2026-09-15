#!/usr/bin/env python3
"""Local GPU0/GPU1 two-process launcher. Use bench_two_peers.py directly for two hosts."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from bench_two_peers import memory_budget, region_counts


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--descriptors', type=int, nargs='+', default=[207360])
    p.add_argument('--mr-count', type=int, default=256)
    p.add_argument('--descriptor-bytes', type=int, default=32256)
    p.add_argument('--inflight', type=int, nargs='+', default=[1])
    p.add_argument('--backends', nargs='+', choices=['MOONCAKE', 'UCX0', 'UCX4'], default=['MOONCAKE', 'UCX0', 'UCX4'])
    p.add_argument('--operation', choices=['READ', 'WRITE'], default='READ')
    p.add_argument('--notify', choices=['on', 'off'], default='on')
    p.add_argument('--warmup-waves', type=int, default=2)
    p.add_argument('--measure-waves', type=int, default=10)
    p.add_argument('--target-device', type=int, default=1)
    p.add_argument('--initiator-device', type=int, default=0)
    p.add_argument('--target-hca', default='rocep139s0')
    p.add_argument('--initiator-hca', default='rocep139s0')
    p.add_argument('--timeout-sec', type=float, default=120)
    p.add_argument('--port', type=int, default=19450)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    script = Path(__file__).with_name('bench_two_peers.py').resolve()
    failures = []
    for n in a.descriptors:
        for h in a.inflight:
            for backend in a.backends:
                name = f'{backend}-N{n}-R{a.mr_count}-H{h}-{a.operation}'
                out = a.output / name
                base = [sys.executable, str(script), '--run-id', name, '--backend', 'UCX' if backend.startswith('UCX') else backend,
                        '--num-threads', '4' if backend == 'UCX4' else '0', '--descriptors', str(n),
                        '--descriptor-bytes', str(a.descriptor_bytes), '--mr-count', str(a.mr_count), '--inflight', str(h),
                        '--warmup-waves', str(a.warmup_waves), '--measure-waves', str(a.measure_waves),
                        '--operation', a.operation, '--notify', a.notify, '--output', str(out),
                        '--port', str(a.port), '--timeout-sec', str(a.timeout_sec)]
                print(json.dumps({'case': name, 'bytes_per_gpu': sum(((c * (a.descriptor_bytes + 16) * h + 128 + 65535) // 65536) * 65536 for c in region_counts(n, a.mr_count)),
                                  'command': base, 'scope': 'same-host forced RDMA; not cross-node'}), flush=True)
                if a.dry_run:
                    continue
                out.mkdir(parents=True, exist_ok=False)
                processes = []
                logs = []
                def counters():
                    values = {}
                    for hca in {a.target_hca, a.initiator_hca}:
                        for sub in ('counters', 'hw_counters'):
                            for path in Path('/sys/class/infiniband', hca, 'ports/1', sub).glob('*'):
                                try:
                                    values[f'{hca}/{sub}/{path.name}'] = int(path.read_text())
                                except (OSError, ValueError):
                                    pass
                    return values
                before = counters()
                def launch(role, device, hca):
                    env = os.environ.copy()
                    env.update(UCX_TLS='rc,cuda_copy', UCX_NET_DEVICES=hca + ':1', UCX_LOG_LEVEL='info', UCX_PROTO_INFO='y',
                               MC_TE_FILTERS=hca, MC_FORCE_HCA='1', MC_INTRA_NVLINK='0',
                               WITH_NVIDIA_PEERMEM='0', NIXL_MOONCAKE_IP_ADDR='127.0.0.1',
                               PYTORCH_NO_CUDA_MEMORY_CACHING='1')
                    if env.get('NIXL_MC_DIAG_ENABLE') == '1':
                        env['NIXL_MC_DIAG_FILE'] = str((out / (role + '-stages.jsonl')).resolve())
                    env.pop('MC_FORCE_TCP', None)
                    env.pop('MC_FORCE_MNNVL', None)
                    log = (out / (role + '.log')).open('w')
                    logs.append(log)
                    proc = subprocess.Popen(base + ['--role', role, '--device', str(device)], env=env,
                                            stdout=log, stderr=subprocess.STDOUT)
                    processes.append(proc)
                    return proc
                try:
                    target = launch('target', a.target_device, a.target_hca)
                    deadline = time.monotonic() + a.timeout_sec
                    while not (out / 'ready').exists():
                        if target.poll() is not None:
                            raise RuntimeError(f'target exited {target.returncode}')
                        if time.monotonic() > deadline:
                            raise TimeoutError('target setup timed out')
                        time.sleep(0.1)
                    initiator = launch('initiator', a.initiator_device, a.initiator_hca)
                    deadline = time.monotonic() + a.timeout_sec * (a.warmup_waves+a.measure_waves+2)
                    while any(proc.poll() is None for proc in processes):
                        if any(proc.poll() not in (None, 0) for proc in processes):
                            raise RuntimeError('peer failed; see role logs')
                        if time.monotonic() > deadline:
                            raise TimeoutError('case watchdog expired')
                        time.sleep(0.1)
                    if any(proc.returncode for proc in processes):
                        raise RuntimeError('peer failed')
                    for role in ('target', 'initiator'):
                        text = (out / (role + '.log')).read_text()
                        if backend == 'MOONCAKE':
                            valid = 'installTransport, type=rdma' in text and 'using TCP transport only' not in text
                        else:
                            valid = 'rma(rc_' in text
                        if not valid:
                            raise RuntimeError(f'{role}: RDMA transport evidence missing')
                    print(f'PASS {name}', flush=True)
                except (RuntimeError, TimeoutError) as exc:
                    failures.append({'case': name, 'error': str(exc)})
                    (out / 'FAILED.json').write_text(json.dumps(failures[-1]))
                    print(f'FAIL {name}: {exc}', flush=True)
                finally:
                    for proc in processes:
                        if proc.poll() is None:
                            proc.kill()
                        proc.wait()
                    for log in logs:
                        log.close()
                    after = counters()
                    (out / 'nic-counters.json').write_text(json.dumps(dict(before=before, after=after,
                        delta={k: after[k]-v for k,v in before.items() if k in after},
                        scope='node HCA counters across full case, including setup and warmup; not process-exclusive'), indent=2))
    if failures:
        sys.exit(1)


if __name__ == '__main__':
    main()
