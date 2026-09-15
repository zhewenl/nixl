# Matching build environment

Exact historical numbers require the revisions in [environment.json](environment.json).
The original test used an existing CUDA 13.1 toolkit/driver and system RDMA verbs,
CMake/GCC, glog/gflags, OpenSSL, JsonCpp and numactl development packages. Dependency
installation is host-specific; no script here changes system packages or routing.
The tested Mooncake fork is private and requires separate access.

Expected checkout-local layout (also accepted under NIXL_DEBUG_PREFIX):

```text
.venv/                    Python 3.12; NIXL Python + native installation
.local/src/Mooncake/       pinned b5ea3f00 source
.local/src/ucx/            pinned 4b7a6ca8 source
.local/mooncake/           installed libtransfer_engine + matching headers
.local/ucx/                UCX 1.20.0 built with CUDA and RDMA
.local/abseil/             Abseil 20250814.1, if required by this TE build
.local/nixl-build/         Meson build, needed by diagnostic plugin builder
```

1. Create `.venv` with Python 3.12 and install the build dependencies:
   `meson==1.12.0`, `meson-python==0.21.1`, `ninja==1.13.2`, `pybind11==3.1.0`,
   `numpy==2.5.3`, `pytest==9.1.1`, plus the matching CUDA PyTorch build (historically
   `torch 2.14.0+cu130`). These are the measured versions, not a claim that every
   package is publicly available on every platform today.
2. Obtain the pinned UCX and Mooncake source revisions. Build/install UCX with
   CUDA and verbs support into `.local/ucx`; the system UCX 1.16 on the test host
   lacked CUDA and was unsuitable. Run `.local/ucx/bin/ucx_info -v` and `-d` to
   confirm the loaded build and transports.
3. Configure/build TE using its matching dependencies; the relevant measured options:

```bash
source mooncake-debug/env.sh
export CMAKE_PREFIX_PATH="$PWD/.local/abseil${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"
cmake -S .local/src/Mooncake/mooncake-transfer-engine -B .local/src/Mooncake/build-local \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$PWD/.local/mooncake" \
  -DBUILD_SHARED_LIBS=ON -DUSE_CUDA=ON -DUSE_HIP=OFF \
  -DENABLE_MULTI_PROTOCOL=OFF -DWITH_METRICS=ON \
  -DUSE_INTRA_NVLINK=OFF -DUSE_MNNVL=OFF -DUSE_HTTP=OFF -DUSE_ETCD=OFF \
  -DBUILD_EXAMPLES=OFF -DBUILD_UNIT_TESTS=OFF -DBUILD_BENCHMARK=OFF \
  -DPython3_EXECUTABLE="$PWD/.venv/bin/python" \
  -Dpybind11_INCLUDE_DIR="$PWD/.venv/lib/python3.12/site-packages/pybind11/include"
cmake --build .local/src/Mooncake/build-local -j16
cmake --install .local/src/Mooncake/build-local
```

4. Build the baseline NIXL into the same virtual environment:

```bash
export CPATH="$PWD/.local/mooncake/include${CPATH:+:$CPATH}"
export LIBRARY_PATH="$PWD/.local/mooncake/lib${LIBRARY_PATH:+:$LIBRARY_PATH}"
meson setup .local/nixl-build --buildtype=release --prefix="$PWD/.venv" --libdir=lib \
  -Denable_plugins=UCX,MOONCAKE -Ducx_path="$PWD/.local/ucx" \
  -Dnixl_cuda_arch_list=100 -Dbuild_tests=false -Dbuild_examples=false
meson compile -C .local/nixl-build
meson install -C .local/nixl-build
python -m pip install --no-deps .local/nixl-build/src/bindings/python/nixl-meta/nixl-1.3.2-py3-none-any.whl
```

SM100 is the measured GB200 build target; select an appropriate architecture for
other hardware and record that difference. Verify the Python import and plugin
list using README.md before running a small GPU test. The manifest records actual
loaded paths and hashes, so a stale system library cannot silently count as the
pinned installation. This recipe describes the original source-build layout;
only the moved harness was rerun when publishing this branch, not a fresh download
and rebuild of all dependencies.
