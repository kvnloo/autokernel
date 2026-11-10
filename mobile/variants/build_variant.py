#!/usr/bin/env python3
"""Build one pinned audio.cpp Android variant and record success or failure."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time

SOURCE_SHA = "a01aed4a15c1da59010e48bc15fb029ecb961975"
DEPS = {
    "OpenCL-Headers": ("v2025.07.22", "8a97ebc88daa3495d6f57ec10bb515224400186f"),
    "OpenCL-ICD-Loader": ("v2025.07.22", "ad770a1b64c6b8d5f2ed4e153f22e4f45939f27f"),
    "Vulkan-Headers": ("v1.4.328", "9a0f3099c8a9607a7c0f3127d8abfdc19a93e8c5"),
    "SPIRV-Headers": ("vulkan-sdk-1.4.328.1", "01e0577914a75a2569c846778c2f93aa8e6feddd"),
}
STOCK_LIBS = {"libc.so", "libm.so", "libdl.so", "liblog.so", "libvulkan.so"}


def output(argv):
    return subprocess.check_output(argv, text=True, stderr=subprocess.STDOUT).strip()


def main(name):
    if name not in {"omp", "armv86", "armv82", "kleidiai", "vulkan", "opencl", "o3lto"}:
        raise ValueError("unknown variant")
    source = Path(os.environ["AUDIOCPP_SOURCE"]).resolve()
    ndk = Path(os.environ["ANDROID_NDK"]).resolve()
    build = source / "build" / ("android-" + name)
    dest = Path(os.environ["VARIANTS_OUT"]).resolve() / name
    dest.mkdir(parents=True, exist_ok=True)
    build.mkdir(parents=True, exist_ok=True)
    tools = ndk / "toolchains/llvm/prebuilt/linux-x86_64"
    jobs = str(int(os.environ.get("BUILD_JOBS", "6")))
    started = time.monotonic()
    record = {
        "variant": name, "built": False, "reason": None,
        "audio_cpp": SOURCE_SHA, "source_license": "Apache-2.0",
        "where_built": socket.gethostname(), "platform": "android-31",
        "abi": "arm64-v8a", "ndk": str(ndk), "compiler_version": None,
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "commands": [], "dependencies": {}, "needed": [], "notes": [],
        "sha256": None, "size_bytes": None, "size_mb": None,
        "cmake_command": None, "flags": [], "device_tested": False,
    }
    log = (dest / "build.log").open("w", buffering=1)

    def run(argv, heavy=False):
        argv = [str(a) for a in argv]
        if heavy and os.environ.get("BUILD_RUNNER", "local") != "fleet":
            lock = Path(os.environ["HEAVY_LOCK"])
            lock.parent.mkdir(parents=True, exist_ok=True)
            argv = ["flock", str(lock), "nice", "-n", "19", *argv]
        command = shlex.join(argv)
        record["commands"].append(command)
        print("+ " + command, flush=True)
        log.write("+ " + command + "\n")
        subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT, check=True)

    def fetch(project):
        tag, sha = DEPS[project]
        path = build / "deps" / project
        if not (path / ".git").exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            run(["git", "clone", "--depth", "1", "--branch", tag,
                 "https://github.com/KhronosGroup/" + project + ".git", path])
        actual = output(["git", "-C", str(path), "rev-parse", "HEAD"])
        if actual != sha:
            raise RuntimeError(f"{project} revision mismatch: {actual} != {sha}")
        if output(["git", "-C", str(path), "status", "--porcelain"]):
            raise RuntimeError(f"{project} source is dirty")
        record["dependencies"][project] = {"tag": tag, "commit": sha}
        return path

    toolchain = [
        f"-DCMAKE_TOOLCHAIN_FILE={ndk}/build/cmake/android.toolchain.cmake",
        "-DANDROID_ABI=arm64-v8a", "-DANDROID_PLATFORM=android-31",
        "-DANDROID_STL=c++_static", "-DCMAKE_BUILD_TYPE=Release",
    ]
    flags = toolchain + [
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON", "-DENGINE_ENABLE_NATIVE_CPU=OFF",
        "-DENGINE_ENABLE_NEON=ON", "-DENGINE_ENABLE_LLAMAFILE=ON",
        "-DENGINE_ENABLE_OPENMP=OFF", "-DGGML_OPENMP=OFF",
        "-DENGINE_ENABLE_VULKAN=OFF", "-DGGML_OPENCL=OFF",
        "-DGGML_CPU_KLEIDIAI=OFF", "-DGGML_CPU_ARM_ARCH=armv8.2-a+dotprod+fp16+i8mm",
        "-DCMAKE_EXE_LINKER_FLAGS=-llog",
    ]

    def setflag(key, value):
        nonlocal flags
        flags = [f for f in flags if not f.startswith("-D" + key + "=")]
        flags.append("-D" + key + "=" + str(value))

    try:
        record["compiler_version"] = output([str(tools / "bin/clang"), "--version"])
        actual_source = output(["git", "-C", str(source), "rev-parse", "HEAD"])
        if actual_source != SOURCE_SHA:
            raise RuntimeError("audio.cpp revision mismatch: " + actual_source)
        if output(["git", "-C", str(source), "status", "--porcelain", "--untracked-files=no"]):
            raise RuntimeError("audio.cpp tracked sources are dirty")
        if name == "omp":
            setflag("ENGINE_ENABLE_OPENMP", "ON")
            setflag("GGML_OPENMP", "ON")
            # NDK's static libomp is selected explicitly, avoiding FindOpenMP's
            # default shared runtime even when -static-openmp is also supplied.
            omp = tools / "lib/clang/21/lib/linux/aarch64/libomp.a"
            if not omp.exists():
                candidates = list((tools / "lib/clang").glob("*/lib/linux/aarch64/libomp.a"))
                if len(candidates) != 1:
                    raise RuntimeError("Cannot uniquely locate NDK aarch64 libomp.a")
                omp = candidates[0]
            for lang in ("C", "CXX"):
                setflag("OpenMP_" + lang + "_FLAGS", "-fopenmp -static-openmp")
                setflag("OpenMP_" + lang + "_LIB_NAMES", "omp")
            setflag("OpenMP_omp_LIBRARY", omp)
            setflag("CMAKE_EXE_LINKER_FLAGS", "-llog -fopenmp -static-openmp")
            record["notes"].append("NDK libomp.a and static libc++; no extra runtime shared library.")
        elif name == "armv86":
            setflag("GGML_CPU_ARM_ARCH", "armv8.6-a+dotprod+fp16+i8mm+bf16")
            record["notes"].append("ARMv8.6 bf16 enabled; no SVE/SVE2/SME.")
        elif name == "armv82":
            # Lenovo TB350FU (MT8781, Cortex-A76/A55): dotprod+fp16, no i8mm/bf16;
            # the i8mm base SIGILLs (exit 132) there.
            setflag("GGML_CPU_ARM_ARCH", "armv8.2-a+dotprod+fp16")
            record["notes"].append("ARMv8.2 dotprod+fp16 only; for cores without i8mm/bf16.")
        elif name == "kleidiai":
            cmake = (source / "external/ggml/src/ggml-cpu/CMakeLists.txt").read_text()
            if "if (GGML_CPU_KLEIDIAI)" not in cmake:
                raise RuntimeError("Vendored ggml does not support GGML_CPU_KLEIDIAI")
            setflag("GGML_CPU_KLEIDIAI", "ON")
            record["dependencies"]["KleidiAI"] = {
                "tag": "v1.24.0", "archive_md5": "2f02ebe29573d45813e671eb304f2a00",
                "url": "https://github.com/ARM-software/kleidiai/releases/download/v1.24.0/kleidiai-v1.24.0-src.tar.gz",
            }
            record["notes"].append("ggml FetchContent downloads hash-checked KleidiAI v1.24.0; NEON dotprod/i8mm only.")
        elif name == "vulkan":
            headers = fetch("Vulkan-Headers")
            spirv_headers = fetch("SPIRV-Headers")
            setflag("ENGINE_ENABLE_VULKAN", "ON")
            setflag("CMAKE_CXX_FLAGS", "-I" + str(spirv_headers / "include"))
            setflag("Vulkan_INCLUDE_DIR", headers / "include")
            setflag("Vulkan_LIBRARY", tools / "sysroot/usr/lib/aarch64-linux-android/31/libvulkan.so")
            setflag("Vulkan_GLSLC_EXECUTABLE", ndk / "shader-tools/linux-x86_64/glslc")
            record["glslc_version"] = output([str(ndk / "shader-tools/linux-x86_64/glslc"), "--version"])
            record["notes"].append("Pinned Vulkan C/C++ and SPIR-V headers; SPIRV-Headers include directory is supplied in CMAKE_CXX_FLAGS because the NDK lacks spirv/unified1/spirv.hpp. NDK host glslc and Android sysroot libvulkan.so; host C/C++ compiler builds shader generator.")
        elif name == "opencl":
            cmake = (source / "external/ggml/CMakeLists.txt").read_text()
            if "GGML_OPENCL_USE_ADRENO_KERNELS" not in cmake:
                raise RuntimeError("Vendored ggml lacks Adreno OpenCL kernels")
            headers = fetch("OpenCL-Headers")
            loader = fetch("OpenCL-ICD-Loader")
            loader_build = build / "icd-build"
            run(["cmake", "-G", "Ninja", "-S", loader, "-B", loader_build,
                 *toolchain, "-DOPENCL_ICD_LOADER_BUILD_SHARED_LIBS=OFF",
                 "-DENABLE_OPENCL_LAYERS=OFF", "-DBUILD_TESTING=OFF",
                 "-DOPENCL_ICD_LOADER_PIC=ON",
                 "-DOPENCL_ICD_LOADER_HEADERS_DIR=" + str(headers)])
            run(["cmake", "--build", loader_build, "--target", "OpenCL", "-j" + jobs], heavy=True)
            setflag("GGML_OPENCL", "ON")
            setflag("GGML_OPENCL_USE_ADRENO_KERNELS", "ON")
            setflag("GGML_OPENCL_EMBED_KERNELS", "ON")
            setflag("OpenCL_INCLUDE_DIR", headers)
            setflag("OpenCL_LIBRARY", loader_build / "libOpenCL.a")
            record["runtime_environment"] = {"OCL_ICD_FILENAMES": "/vendor/lib64/libOpenCL.so"}
            record["runtime_backend_option"] = "--backend best"
            record["notes"].append("Static Khronos ICD loader uses dlopen; set OCL_ICD_FILENAMES=/vendor/lib64/libOpenCL.so on device. Vendor namespace/ICD compatibility untested; no bundled stub or vendor DSO.")
            record["notes"].append("audio.cpp has no explicit --backend opencl parser entry; --backend best calls ggml_backend_init_best and can select the registered OpenCL GPU. Confirm actual selection on device; CPU fallback is possible.")
        elif name == "o3lto":
            for lang in ("C", "CXX"):
                setflag("CMAKE_" + lang + "_FLAGS_RELEASE", "-O3 -DNDEBUG -flto=thin")
            setflag("CMAKE_EXE_LINKER_FLAGS", "-llog -flto=thin -Wl,--thinlto-jobs=" + jobs)
            record["notes"].append("Explicit -O3, ThinLTO, bounded linker workers; static libc++.")
        # Preserve the original uncached OpenMP recipe; the other variants share
        # non-OpenMP objects and benefit from the optional cross-tree cache.
        cache_program = None if name == "omp" else (os.environ.get("CCACHE_PROGRAM") or shutil.which("ccache"))
        local_cache = source / "build/android-tools/ccache-4.14.2-linux-x86_64-glibc/ccache"
        if name != "omp" and not cache_program and local_cache.is_file():
            cache_program = str(local_cache)
        if cache_program:
            os.environ.setdefault("CCACHE_DIR", str(source / "build/android-tools/cache"))
            os.environ.setdefault("CCACHE_BASEDIR", str(source))
            os.environ.setdefault("CCACHE_NOHASHDIR", "true")
            for lang in ("C", "CXX"):
                setflag("CMAKE_" + lang + "_COMPILER_LAUNCHER", cache_program)
            record["compiler_cache"] = {
                "version": output([cache_program, "--version"]).splitlines()[0],
                "environment": {k: os.environ[k] for k in ("CCACHE_DIR", "CCACHE_BASEDIR", "CCACHE_NOHASHDIR")},
            }
        record["flags"] = flags
        command = ["cmake", "-G", "Ninja", "-S", str(source), "-B", str(build), *flags]
        record["cmake_command"] = shlex.join(command)
        run(command)
        cache = {}
        for line in (build / "CMakeCache.txt").read_text().splitlines():
            match = re.match(r"([^/#][^:]*):[^=]+=(.*)", line)
            if match and (match[1].startswith(("GGML_", "ENGINE_ENABLE_", "CMAKE_C_FLAGS", "CMAKE_CXX_FLAGS", "ANDROID_", "Vulkan_", "OpenMP_", "OpenCL_"))):
                cache[match[1]] = match[2]
        record["effective_cache"] = cache
        run(["cmake", "--build", build, "--target", "audiocpp_cli", "-j" + jobs], heavy=True)
        binary = dest / "audiocpp_cli"
        # copy2 is required for large artifacts; do not use shell cp.
        shutil.copy2(build / "bin/audiocpp_cli", binary)
        run([tools / "bin/llvm-strip", "--strip-all", binary])
        desc = output(["file", str(binary)])
        dynamic = output([str(tools / "bin/llvm-readelf"), "-d", str(binary)])
        needed = re.findall(r"\(NEEDED\).*?\[(.*?)\]", dynamic)
        record.update(file=desc, needed=needed, readelf_dynamic=dynamic,
                      size_bytes=binary.stat().st_size,
                      size_mb=round(binary.stat().st_size / 1_000_000, 6),
                      sha256=hashlib.sha256(binary.read_bytes()).hexdigest())
        if not ("ELF 64-bit" in desc and "ARM aarch64" in desc and ", stripped" in desc):
            raise RuntimeError("Binary is not a stripped AArch64 ELF: " + desc)
        if set(needed) - STOCK_LIBS:
            raise RuntimeError("Non-stock Android DT_NEEDED: " + str(set(needed) - STOCK_LIBS))
        record["built"] = True
        record["reason"] = "Cross-build, strip, ELF architecture, and Android DT_NEEDED checks passed; not device-tested."
    except Exception as exc:
        record["reason"] = str(exc)
        log.flush()
        record["failure_log_tail"] = (dest / "build.log").read_text(errors="replace")[-14000:]
        # A failed rerun must not leave a stale binary masquerading as this build.
        (dest / "audiocpp_cli").unlink(missing_ok=True)
        print(f"{name}: FAILED: {exc}", file=sys.stderr, flush=True)
    finally:
        record["build_seconds"] = round(time.monotonic() - started, 3)
        record["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        (dest / "BUILD.json").write_text(json.dumps(record, indent=2) + "\n")
        log.close()
    print(json.dumps({k: record.get(k) for k in ("variant", "built", "reason", "size_mb", "sha256", "needed")}), flush=True)
    return 0 if record["built"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
