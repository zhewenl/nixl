#!/usr/bin/env bash
set -eu
out_dir="${1:?usage: collect_env.sh OUTPUT_DIRECTORY}"
mkdir -p "$out_dir"
python - "$out_dir" <<'PY'
import json, os, pathlib, subprocess, sys
root = pathlib.Path(os.environ['NIXL_REPRO_ROOT'])
def command(args):
    try:
        p = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
        return {'exit_code':p.returncode, 'output':p.stdout}
    except Exception as e:
        return {'unavailable':str(e)}
prefix = pathlib.Path(os.environ.get('NIXL_DEBUG_PREFIX', str(root)))
manifest = {'affinity': sorted(os.sched_getaffinity(0))}
for name, args in {
    'nixl_commit':['git','-C',str(root),'rev-parse','HEAD'],
    'mooncake_commit':['git','-C',str(prefix/'.local/src/Mooncake'),'rev-parse','HEAD'],
    'ucx_commit':['git','-C',str(prefix/'.local/src/ucx'),'rev-parse','HEAD'],
    'ucx_version':['ucx_info','-v'], 'ucx_devices':['ucx_info','-d'],
    'gpu':['nvidia-smi','-q'], 'topology':['nvidia-smi','topo','-m'],
    'rdma':['rdma','link'], 'ibv':['ibv_devinfo'], 'cpu':['lscpu'],
    'kernel':['uname','-a'], 'packages':['uv','pip','freeze','--python',sys.executable],
}.items():
    manifest[name] = command(args)
for path in ['/proc/self/status','/proc/self/cgroup','/sys/fs/cgroup/cpu.max']:
    try: manifest[path] = pathlib.Path(path).read_text()
    except OSError: manifest[path] = 'unavailable'
pathlib.Path(sys.argv[1], 'environment.json').write_text(json.dumps(manifest,indent=2))
PY
