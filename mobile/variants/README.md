# Android arm64 audio.cpp variants

Reproducible cross-builds for the Galaxy S25 Ultra (SM8750, Snapdragon 8 Elite).
These artifacts are **not device-tested**. No phone commands are used by the builder.
All variants retain audio.cpp's full model composite and the base NEON/llamafile
settings, Android API 31, static libc++, and the explicit ARM ISA (not host-native).

Source: audio.cpp `a01aed4a15c1da59010e48bc15fb029ecb961975` (Apache-2.0).
Existing `build/android-arm64` and the registered `base` artifact are never changed.

## Variant status

| Variant | Change from base | Status |
| --- | --- | --- |
| `omp` | Engine **and ggml** OpenMP ON; NDK `libomp.a` | Built, 28.340224 MB |
| `armv86` | `armv8.6-a+dotprod+fp16+i8mm+bf16`; no SVE/SME | Built, 27.825416 MB |
| `kleidiai` | `GGML_CPU_KLEIDIAI=ON`, pinned v1.24.0 | Built, 27.870648 MB |
| `vulkan` | Engine Vulkan ON; pinned Vulkan/SPIR-V headers, NDK host `glslc` | Built, 49.206296 MB |
| `opencl` | Adreno OpenCL kernels, embedded kernels, static ICD loader | Built, 29.217472 MB |
| `o3lto` | Explicit `-O3 -DNDEBUG -flto=thin` and ThinLTO linker | Built, 28.063936 MB |

## Build host

The preferred MBP was attempted through `fleet-build` twice, including a 1 GiB
preflight; its queue was held (exit 75). Its home/root filesystem had only 2.7 GiB
free versus a 2.3 GiB NDK before any build outputs. No NDK was staged and no remote
data was removed. Builds therefore used the task-authorized **groot** fallback,
under the shared heavy lock, niceness 19, and `-j6`. Lock waits are included in
`build_seconds`. Ccache 4.14.2 was used after the first OpenMP build; its official
Linux archive SHA-256 was checked against GitHub's release digest
`bd1313f5316f594443dcb4113a4191c666ac6450e0cf016ff8cb8b080f46c160`.

## Build

Requires Linux x86-64, CMake >= 3.20, Ninja, Python 3, Git, `file`, and NDK
`30.0.16248370`. Vulkan also needs native host C/C++ compilers. Network access is
needed on first use for the pinned dependencies. No root installation is needed.

```bash
# Run in an admitted fleet-build job with the source and NDK staged there:
BUILD_RUNNER=fleet \
AUDIOCPP_SOURCE=/path/to/audio.cpp \
ANDROID_NDK=/path/to/ndk/30.0.16248370 \
VARIANTS_OUT=/path/to/variants \
mobile/variants/build_variants.sh

# Only after fleet is unavailable and local fallback is authorized:
ALLOW_LOCAL_BUILD=1 mobile/variants/build_variants.sh

# Rebuild any subset (incremental and safe to repeat):
ALLOW_LOCAL_BUILD=1 mobile/variants/build_variants.sh omp opencl
```

`build_variants.sh` has one function per variant; `build_variant.py` handles
configuration, dependencies, build, strip, verification, and provenance. Each build
uses its own `<source>/build/android-<variant>` directory. It checks the exact source
revision and refuses modified tracked source files. Dependency checkouts are pinned
and must be clean. It does not modify source code or reuse the existing base build.

`BUILD_JOBS` defaults to 6. Local heavy builds execute as:

```text
flock /home/kvn/.hermes/profiles/clean/cache/scratch/heavy.lock nice -n 19 cmake --build <dir> --target audiocpp_cli -j6
```

`HEAVY_LOCK` can override the shared lock path for another host. The lock is never
bypassed on a local build. `BUILD_RUNNER=fleet` is only for an already scheduler-
admitted job; it is not an alternate local admission mechanism.

The default source, NDK, and output paths match the owner workspace. Override them
using `AUDIOCPP_SOURCE`, `ANDROID_NDK`, and `VARIANTS_OUT`. Set `CCACHE_PROGRAM` to an
optional ccache executable; otherwise the builder detects `ccache` on PATH, then
`<source>/build/android-tools/ccache-4.14.2-linux-x86_64-glibc/ccache`. Ccache is not
required. `omp` preserves its original uncached recipe; the remaining variants
can reuse non-OpenMP objects. The build record captures any cache version/environment used. Source-
relative ccache base paths and disabled cwd hashing allow stripped object reuse
between isolated build trees; compiler options remain part of the cache key.

Each output directory contains:

- `BUILD.json`: success/failure and reason, exact CMake command/flags and all invoked
  build/dependency commands, source/NDK/compiler versions, dependency pins, host,
  UTC timestamps and wall build seconds (including any lock wait), SHA-256, decimal
  MB/byte size, `file` output, and full `llvm-readelf -d` output/NEEDED list.
- `build.log`: configure and build output, including failures.
- `audiocpp_cli`: only when the build and verification succeeded; copied using
  Python `shutil.copy2`, then stripped using the NDK's `llvm-strip --strip-all`.

A failed rerun removes the stale destination executable, records `built=false`,
and continues to the next requested variant. The batch exits nonzero if any variant
fails. No binary or model files are committed to this repository.

## Backend details and limits

### OpenMP

Audio.cpp's `ENGINE_ENABLE_OPENMP` does **not** control `GGML_OPENMP`; both are set
explicitly. `FindOpenMP` receives `-fopenmp -static-openmp` and the absolute path to
the NDK's AArch64 `libomp.a` for both C and C++. Final linking also selects static
OpenMP. Compilation may warn that `-static-openmp` is unused until link time.

### ARMv8.6 / KleidiAI

The explicit ISA excludes SVE, SVE2, and SME, which this device lacks. ARMv8.6 enables
BF16. KleidiAI support exists in vendored ggml and downloads v1.24.0 through ggml's
FetchContent URL with upstream's MD5 `2f02ebe29573d45813e671eb304f2a00`.
Only dotprod/i8mm NEON kernels are selected; no ARM native-host autodetection.

### Vulkan

The NDK sysroot supplies the Android API 31 `libvulkan.so` link stub. C++ Vulkan
headers come from Khronos Vulkan-Headers v1.4.328, commit
`9a0f3099c8a9607a7c0f3127d8abfdc19a93e8c5`. The initial backend compile failed on
missing `spirv/unified1/spirv.hpp`; the corrected recipe also fetches SPIRV-Headers
`vulkan-sdk-1.4.328.1`, commit `01e0577914a75a2569c846778c2f93aa8e6feddd`, and
adds its `include` directory through `CMAKE_CXX_FLAGS`. No audio.cpp/ggml source patch
is needed. `Vulkan_INCLUDE_DIR`, `Vulkan_LIBRARY`, and `Vulkan_GLSLC_EXECUTABLE` are
passed explicitly. The shader generator is built
for the Linux host, not Android. The NDK host shader compiler is shaderc v2022.3;
its feature probes reject cooperative matrices, integer-dot-product, and BF16
extensions, so those optional Vulkan shader paths are disabled. CPU BF16 experiments
are available in `armv86` separately.

### OpenCL

Vendored ggml supports `GGML_OPENCL`, `GGML_OPENCL_USE_ADRENO_KERNELS`, and embedding
kernels. These GGML cache options pass through audio.cpp without an ENGINE wrapper.
No Qualcomm driver is copied from a phone. The build fetches:

- OpenCL-Headers v2025.07.22, `8a97ebc88daa3495d6f57ec10bb515224400186f`.
- OpenCL-ICD-Loader v2025.07.22, `ad770a1b64c6b8d5f2ed4e153f22e4f45939f27f`.

The ICD loader is a **real static loader**, not a fake implementation or unresolved
linker stub. It dynamically loads the vendor ICD at runtime. The parent device-test
runner must provide:

```text
OCL_ICD_FILENAMES=/vendor/lib64/libOpenCL.so
```

This environment variable is `OCL_ICD_FILENAMES`, not `OPENCL_ICD_FILENAMES`.
Audio.cpp's CLI does not accept `--backend opencl`; use `--backend best`, whose
`BestAvailable` path calls `ggml_backend_init_best()`. This build has OpenCL as its
only compiled GPU backend, but a missing/inaccessible GPU can fall back to CPU.
Confirm actual backend selection in the device output rather than treating a
successful process exit as proof of OpenCL execution.
Vendor linker namespace access, driver ICD compatibility, backend enumeration,
operator coverage, numerical correctness, latency, and thermal behavior remain
on-device gates. The hardware profile also lists `libOpenCL_adreno.so`; do not assume
that either vendor path is visible from every Android execution context.

### Dynamic dependency gate

Every successful artifact must be a stripped 64-bit AArch64 ELF. `llvm-readelf -d`
rejects NEEDED entries outside `libc.so`, `libm.so`, `libdl.so`, `liblog.so`, and
`libvulkan.so` (the last for Vulkan only). Neither `libc++_shared.so`, `libomp.so`, nor
`libOpenCL.so` may appear in DT_NEEDED. The static ICD loader's vendor `dlopen` is
intentionally separate from this dependency check. Successful cross-linking does
not prove Android runtime access to the vendor library.

## Verification

```bash
bash -n mobile/variants/build_variants.sh
env -u PYTHONPATH -u PYTHONHOME python3 mobile/variants/test_build_variants.py -v
# Read every requested variant back, recompute hash/size, and rerun file/readelf:
env -u PYTHONPATH -u PYTHONHOME python3 mobile/variants/verify_variants.py
# Optionally preserve the exact external BUILD.json files in this repo:
env -u PYTHONPATH -u PYTHONHOME python3 mobile/variants/verify_variants.py --receipts-dir mobile/variants/receipts
```

`receipts/*.json` preserves the actual build records from this run. The binaries
and complete build logs remain in the external variant store, not Git.

The lightweight tests exercise admission refusal, invalid variant input, failure
records/stale-output removal, and batch continuation through the actual CLI.
They do not substitute for the real cross-build, ELF, SHA-256, and NEEDED checks.

This run passed all four interface tests, `bash -n`, and read-back verification of
all six real artifacts. Incremental reruns of `omp` and `kleidiai` to a separate
scratch output directory produced the identical SHA-256 values as their registered
artifacts. These checks did not execute any Android binary or connect to a phone.
