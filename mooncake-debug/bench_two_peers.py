#!/usr/bin/env python3
"""Two-process GPU descriptor benchmark. Control messages are outside timed waves."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import time
import traceback

GUARD = 64


def region_counts(n, regions):
    if not 0 < regions <= n:
        raise ValueError('require 0 < mr-count <= descriptors')
    q, r = divmod(n, regions)
    return [q + (i < r) for i in range(regions)]


def memory_budget(n, length, gap, regions, inflight):
    return n * (length + gap) * inflight + regions * GUARD * 2


def descriptor_rows(addresses, counts, length, gap, slot, device):
    stride = length + gap
    return [(base + GUARD + (slot * count + i) * stride, length, device)
            for base, count in zip(addresses, counts) for i in range(count)]


def timed(fn, *args, **kwargs):
    cpu = time.thread_time_ns()
    wall = time.perf_counter_ns()
    result = fn(*args, **kwargs)
    end = time.perf_counter_ns()
    return result, {'wall_ms': (end - wall) / 1e6,
                    'thread_cpu_ms': (time.thread_time_ns() - cpu) / 1e6,
                    'start_ns': wall, 'end_ns': end}


def send(sock, message):
    data = json.dumps(message).encode()
    sock.sendall(len(data).to_bytes(8, 'big') + data)


def recv(sock):
    def exact(n):
        chunks = bytearray()
        while len(chunks) < n:
            chunk = sock.recv(n - len(chunks))
            if not chunk:
                raise EOFError('peer closed control channel')
            chunks.extend(chunk)
        return chunks
    n = int.from_bytes(exact(8), 'big')
    if n > 128 * 1024 * 1024:
        raise ValueError('oversized control message')
    return json.loads(exact(n))


def loaded_libraries():
    names = set()
    for line in Path('/proc/self/maps').read_text().splitlines():
        path = line.split()[-1]
        if path.startswith('/') and any(s in path for s in
                ('libplugin_', 'libnixl', 'libmooncake_common', 'libasio.', 'libtransfer_engine', 'libucp.', 'libuct.', 'libucs.', 'libuct_cuda', 'libuct_ib', 'libuct_mlx5')):
            names.add(path)
    return [{'path': p, 'sha256': hashlib.file_digest(open(p, 'rb'), 'sha256').hexdigest()}
            for p in sorted(names)]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--role', choices=['target', 'initiator'], required=True)
    p.add_argument('--peer-host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=19450)
    p.add_argument('--run-id', default='local')
    p.add_argument('--backend', choices=['UCX', 'MOONCAKE'], required=True)
    p.add_argument('--num-threads', type=int, choices=[0, 1, 2, 4], default=0)
    p.add_argument('--progress-thread', choices=['on', 'off'], default='off')
    p.add_argument('--device', type=int, default=0)
    p.add_argument('--descriptors', type=int, default=207360)
    p.add_argument('--descriptor-bytes', type=int, default=32256)
    p.add_argument('--gap-bytes', type=int, default=16)
    p.add_argument('--mr-count', type=int, default=256)
    p.add_argument('--inflight', type=int, default=1)
    p.add_argument('--operation', choices=['READ', 'WRITE'], default='READ')
    p.add_argument('--notify', choices=['on', 'off'], default='on')
    p.add_argument('--warmup-waves', type=int, default=2)
    p.add_argument('--measure-waves', type=int, default=10)
    p.add_argument('--poll-us', type=int, default=100)
    p.add_argument('--timeout-sec', type=float, default=120)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    counts = region_counts(a.descriptors, a.mr_count)
    if a.backend == 'MOONCAKE' and a.num_threads:
        p.error('baseline Mooncake does not implement num_threads')
    if min(a.descriptor_bytes, a.gap_bytes, a.inflight, a.measure_waves, a.timeout_sec) <= 0:
        p.error('length, gap, inflight, measure-waves and timeout must be positive')
    if min(a.warmup_waves, a.poll_us) < 0:
        p.error('warmup and poll-us must be nonnegative')
    a.output.mkdir(parents=True, exist_ok=True)
    records = []
    raw_path = a.output / (a.role + '.jsonl')
    def flush():
        with raw_path.open('a') as f:
            for record in records:
                f.write(json.dumps(record) + '\n')
        records.clear()
    if raw_path.exists():
        raise FileExistsError(raw_path)
    def record(api, timing, **fields):
        records.append(dict(api=api, run_id=a.run_id, role=a.role, backend=a.backend,
                            num_threads=a.num_threads, **timing, **fields))
    try:
        import numpy as np
        import torch
        from nixl._api import nixl_agent, nixl_agent_config
        import nixl._api as api_module
        torch.set_num_threads(1)
        torch.cuda.set_device(a.device)
        required = memory_budget(a.descriptors, a.descriptor_bytes, a.gap_bytes, a.mr_count, a.inflight)
        free, total = torch.cuda.mem_get_info()
        if required + 1024**3 > free:
            raise RuntimeError(f'insufficient GPU memory: require {required} + 1GiB, free {free}')
        cfg = nixl_agent_config(enable_prog_thread=a.progress_thread == 'on',
                                capture_telemetry=True, num_threads=a.num_threads,
                                backends=[a.backend])
        agent, t = timed(nixl_agent, a.run_id + '-' + a.role, cfg)
        if a.backend not in agent.backends:
            raise RuntimeError('requested backend not loaded')
        record('agent_init', t)
        stride = a.descriptor_bytes + a.gap_bytes
        def allocate():
            return [torch.empty(((count * stride * a.inflight + 2 * GUARD + 65535) // 65536) * 65536,
                                device=f'cuda:{a.device}', dtype=torch.uint8) for count in counts]
        buffers, t = timed(allocate)
        torch.cuda.synchronize()
        record('allocation', t)
        addresses = [buf.data_ptr() for buf in buffers]
        reg, t = timed(agent.register_memory, buffers)
        record('register_memory', t, nixl_regions=len(buffers), actual_backend_mr_count='unavailable')
        patterns = [torch.arange(count, device=f'cuda:{a.device}', dtype=torch.int32)
                    for count in counts]
        def pattern(region, slot, wave):
            return ((patterns[region] + region * 17 + slot * 29 + wave * 31) % 251 + 1).to(torch.uint8)
        is_source = (a.operation == 'READ') == (a.role == 'target')
        def initialize(wave):
            for r, (buf, count) in enumerate(zip(buffers, counts)):
                buf.fill_(253)
                slots = buf[GUARD:GUARD + a.inflight * count * stride].view(a.inflight, count, stride)
                for h in range(a.inflight):
                    payload = slots[h, :, :a.descriptor_bytes]
                    if is_source:
                        payload.copy_(pattern(r, h, wave)[:, None])
                    else:
                        payload.zero_()
            torch.cuda.synchronize()
        def validate(wave):
            checks = []
            for r, (buf, count) in enumerate(zip(buffers, counts)):
                checks += [(buf[:GUARD] == 253).all(), (buf[GUARD + a.inflight * count * stride:] == 253).all()]
                slots = buf[GUARD:GUARD + a.inflight * count * stride].view(a.inflight, count, stride)
                for h in range(a.inflight):
                    checks.append((slots[h, :, :a.descriptor_bytes] == pattern(r, h, wave)[:, None]).all())
                    checks.append((slots[h, :, a.descriptor_bytes:] == 253).all())
            if not torch.stack(checks).all().item():
                raise AssertionError(f'full payload/guard validation failed at wave {wave}')
            torch.cuda.synchronize()
        sock = socket.socket()
        sock.settimeout(a.timeout_sec)
        if a.role == 'target':
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((a.peer_host, a.port))
            sock.listen(1)
            (a.output / 'ready').write_text(str(os.getpid()))
            listener = sock
            sock, _ = listener.accept()
            sock.settimeout(a.timeout_sec)
            listener.close()
        else:
            sock.connect((a.peer_host, a.port))
        shape = {k: getattr(a, k) for k in ('run_id', 'backend', 'num_threads', 'progress_thread',
                 'descriptors', 'descriptor_bytes', 'gap_bytes', 'mr_count', 'inflight', 'operation',
                 'notify', 'warmup_waves', 'measure_waves')}
        metadata, t = timed(agent.get_agent_metadata)
        record('get_metadata', t)
        mine = dict(shape=shape, addresses=addresses, device=a.device,
                    metadata=base64.b64encode(metadata).decode())
        if a.role == 'initiator':
            send(sock, mine)
            peer = recv(sock)
        else:
            peer = recv(sock)
            send(sock, mine)
        if peer['shape'] != shape:
            raise ValueError('peer configuration mismatch')
        peer_name, t = timed(agent.add_remote_agent, base64.b64decode(peer['metadata']))
        record('load_metadata', t)
        prepped = []
        indices = np.arange(a.descriptors, dtype=np.int64)
        if a.role == 'initiator':
            for h in range(a.inflight):
                local = agent.get_xfer_descs(np.asarray(descriptor_rows(addresses, counts, a.descriptor_bytes,
                                                    a.gap_bytes, h, a.device), dtype=np.uint64), 'VRAM')
                remote = agent.get_xfer_descs(np.asarray(descriptor_rows(peer['addresses'], counts,
                    a.descriptor_bytes, a.gap_bytes, h, peer['device']), dtype=np.uint64), 'VRAM')
                lp, t = timed(agent.prep_xfer_dlist, '', local)
                record('prep_local_dlist', t, handle=h)
                rp, t = timed(agent.prep_xfer_dlist, peer_name, remote)
                record('prep_remote_dlist', t, handle=h)
                prepped.append((lp, rp))
        manifest = dict(config={k: str(v) if isinstance(v, Path) else v for k,v in vars(a).items()},
            pid=os.getpid(), hostname=socket.gethostname(), torch=torch.__version__, cuda=torch.version.cuda,
            nixl_python=api_module.__file__, buffer_addresses=addresses, required_bytes=required,
            regions_requested=len(buffers), actual_backend_mr_count='unavailable',
            allocation_bytes=sum(buf.numel() for buf in buffers), allocation_alignment=65536,
            affinity=sorted(os.sched_getaffinity(0)), libraries=loaded_libraries(),
            native_thread_ids=sorted(int(p.name) for p in Path('/proc/self/task').iterdir()),
            environment={k:v for k,v in os.environ.items() if k in ('UCX_TLS', 'UCX_NET_DEVICES', 'UCX_LOG_LEVEL', 'UCX_PROTO_INFO', 'MC_TE_FILTERS',
                          'MC_FORCE_HCA', 'MC_FORCE_TCP', 'MC_INTRA_NVLINK', 'MC_GID_INDEX', 'MC_WORKERS_PER_CTX',
                          'WITH_NVIDIA_PEERMEM', 'NIXL_MOONCAKE_IP_ADDR', 'NIXL_PLUGIN_DIR',
                          'PYTORCH_NO_CUDA_MEMORY_CACHING', 'CUDA_VISIBLE_DEVICES',
                          'NIXL_MC_FINE_STRIDE', 'NIXL_MC_FINE_TIMERS', 'NIXL_MC_DIAG_ENABLE', 'NIXL_MC_DIAG_FILE', 'NIXL_MC_SUBMIT_THREADS')})
        (a.output / (a.role + '-manifest.json')).write_text(json.dumps(manifest, indent=2))
        flush()
        for wave in range(a.warmup_waves + a.measure_waves):
            warmup = wave < a.warmup_waves
            _, t = timed(initialize, wave)
            record('initialize', t, wave=wave, warmup=warmup)
            tags = [f'{a.run_id}/{wave}/{h}' for h in range(a.inflight)]
            if a.role == 'target':
                send(sock, {'ready_wave': wave})
                notices = []
                deadline = time.monotonic() + a.timeout_sec
                # Keep UCX target progress alive while waiting for the control ACK.
                import select
                while not select.select([sock], [], [], 0)[0]:
                    for msgs in agent.get_new_notifs().values():
                        notices.extend(m.decode() for m in msgs)
                    if time.monotonic() > deadline:
                        raise TimeoutError('target waiting for completion')
                    time.sleep(0.0001)
                if recv(sock) != {'completed_wave': wave}:
                    raise ValueError('unexpected completion ACK')
                if a.notify == 'on':
                    while len(notices) < len(tags):
                        for msgs in agent.get_new_notifs().values():
                            notices.extend(m.decode() for m in msgs)
                        if time.monotonic() > deadline:
                            raise TimeoutError('missing transfer notifications')
                        time.sleep(0.0001)
                    if sorted(notices) != sorted(tags):
                        raise AssertionError(f'incorrect notifications: {notices}')
                elif notices:
                    raise AssertionError('unexpected notification in notify-off case')
                _, t = timed(validate, wave)
                record('validate', t, wave=wave, warmup=warmup, full_payload=True, guards=True,
                       notifications=notices)
                send(sock, {'validated_wave': wave})
            else:
                if recv(sock) != {'ready_wave': wave}:
                    raise ValueError('unexpected ready ACK')
                handles = []
                for h, (lp, rp) in enumerate(prepped):
                    handle, t = timed(agent.make_prepped_xfer, a.operation, lp, indices, rp, indices,
                                      tags[h].encode() if a.notify == 'on' else b'')
                    record('make_prepped_xfer', t, wave=wave, handle=h, warmup=warmup)
                    if agent.query_xfer_backend(handle) != a.backend:
                        raise AssertionError('wrong backend')
                    handles.append(handle)
                statuses = []
                process_cpu = time.process_time_ns()
                wave_start = time.perf_counter_ns()
                for h, handle in enumerate(handles):
                    status, t = timed(agent.transfer, handle)
                    record('post', t, wave=wave, handle=h, warmup=warmup, status=status)
                    statuses.append(status)
                    if status not in ('DONE', 'PROC'):
                        raise RuntimeError(f'post failed: {statuses}')
                post_end = time.perf_counter_ns()
                deadline = time.monotonic() + a.timeout_sec
                while 'PROC' in statuses:
                    for h, handle in enumerate(handles):
                        if statuses[h] != 'PROC':
                            continue
                        status, t = timed(agent.check_xfer_state, handle)
                        record('check', t, wave=wave, handle=h, warmup=warmup, status=status)
                        statuses[h] = status
                        if status not in ('DONE', 'PROC'):
                            raise RuntimeError(f'check failed: {statuses}')
                    if time.monotonic() > deadline:
                        raise TimeoutError(f'pending handles: {statuses}')
                    if 'PROC' in statuses and a.poll_us:
                        time.sleep(a.poll_us / 1e6)
                wave_end = time.perf_counter_ns()
                record('wave', {'wall_ms': (wave_end-wave_start)/1e6, 'start_ns': wave_start,
                       'end_ns': wave_end, 'process_cpu_ms': (time.process_time_ns()-process_cpu)/1e6},
                       wave=wave, warmup=warmup, post_burst_ms=(post_end-wave_start)/1e6,
                       payload_bytes=a.descriptors*a.descriptor_bytes*a.inflight)
                for h, handle in enumerate(handles):
                    tele = agent.get_xfer_telemetry(handle)
                    values = {k: getattr(tele, k) for k in ('descCount','totalBytes','postDuration','xferDuration')}
                    if values['descCount'] != a.descriptors or values['totalBytes'] != a.descriptors*a.descriptor_bytes:
                        raise AssertionError(f'telemetry mismatch: {values}')
                    record('telemetry', {}, wave=wave, handle=h, warmup=warmup, **values)
                    _, t = timed(agent.release_xfer_handle, handle)
                    record('release', t, wave=wave, handle=h, warmup=warmup)
                send(sock, {'completed_wave': wave})
                _, t = timed(validate, wave)
                record('validate', t, wave=wave, warmup=warmup, full_payload=True, guards=True)
                if recv(sock) != {'validated_wave': wave}:
                    raise ValueError('missing validation ACK')
            flush()
        # Both endpoints remain alive through the last notification/validation check.
        if a.role == 'initiator':
            send(sock, {'finished': True})
            recv(sock)
        else:
            recv(sock)
            send(sock, {'finished': True})
        manifest['libraries_after_transfer'] = loaded_libraries()
        (a.output / (a.role + '-manifest.json')).write_text(json.dumps(manifest, indent=2))
        (a.output / (a.role + '-success.json')).write_text(json.dumps({'waves': wave+1, 'full_validation': True}))
        print(f'{a.role}: {wave+1} waves passed full data/guard/telemetry checks', flush=True)
        # Process exit performs driver cleanup. Do not run Python buffer/agent destructors in arbitrary order.
        import ctypes
        ctypes.CDLL(None).fflush(None)
        os._exit(0)
    except BaseException:
        record('failure', {}, error=traceback.format_exc())
        flush()
        traceback.print_exc()
        sys.stderr.flush()
        # Never release/reuse an allocation while a failed transfer may still own it.
        os._exit(1)


if __name__ == '__main__':
    main()
