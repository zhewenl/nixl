#!/usr/bin/env python3
"""Build an isolated plugin variant using this checkout's original Meson commands."""
import json
from pathlib import Path
import shlex
import subprocess
import sys
root = Path(__file__).resolve().parents[2]
build = root / '.local/nixl-build'
source, output = map(lambda x: Path(x).resolve(), sys.argv[1:3])
output.parent.mkdir(parents=True, exist_ok=True)
commands = json.loads((build / 'compile_commands.json').read_text())
objects = []
for filename in ('mooncake_backend.cpp', 'mooncake_plugin.cpp'):
    command = next(c for c in commands if c['file'].endswith('/'+filename))
    args = shlex.split(command['command'])
    obj = output.parent / (filename + '.o')
    args[args.index('-o')+1] = str(obj)
    args[args.index('-MF')+1] = str(obj)+'.d'
    args[-1] = str(source / filename)
    args += ['-I'+str(source)]
    subprocess.run(args, cwd=build, check=True)
    objects.append(str(obj))
lines = subprocess.check_output([str(root/'.venv/bin/ninja'), '-C', str(build), '-t', 'commands',
    'src/plugins/mooncake/libplugin_MOONCAKE.so'], text=True).splitlines()
args = shlex.split(lines[-1])
args[args.index('-o')+1] = str(output)
for i, arg in enumerate(args):
    if arg.endswith('mooncake_backend.cpp.o'): args[i] = objects[0]
    if arg.endswith('mooncake_plugin.cpp.o'): args[i] = objects[1]
    if arg.startswith('-Wl,-rpath,'): args[i] = '-Wl,-rpath,'+str(root/'.venv/lib')
subprocess.run(args, cwd=build, check=True)
