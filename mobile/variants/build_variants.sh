#!/usr/bin/env bash
# Cross-build only: this script never connects to an Android device.
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export AUDIOCPP_SOURCE=${AUDIOCPP_SOURCE:-/mnt/zer0models/models/zlv-tts/audio.cpp}
export ANDROID_NDK=${ANDROID_NDK:-/home/kvn/android/sdk/ndk/30.0.16248370}
export VARIANTS_OUT=${VARIANTS_OUT:-/mnt/zer0models/models/zlv-tts/phone-bundle/variants}
export BUILD_JOBS=${BUILD_JOBS:-6}
export HEAVY_LOCK=${HEAVY_LOCK:-/home/kvn/.hermes/profiles/clean/cache/scratch/heavy.lock}
# Local builds require the owner's explicit fallback admission. For a fleet job,
# set BUILD_RUNNER=fleet; the fleet scheduler supplies the resource boundary.
if [[ ${BUILD_RUNNER:-local} != fleet && ${ALLOW_LOCAL_BUILD:-0} != 1 ]]; then
    printf '%s\n' 'Use fleet-build, or set ALLOW_LOCAL_BUILD=1 after fleet is unavailable.' >&2
    exit 2
fi
build_one() { env -u PYTHONPATH -u PYTHONHOME python3 "$HERE/build_variant.py" "$1"; }
build_omp() { build_one omp; }
build_armv86() { build_one armv86; }
build_armv82() { build_one armv82; }
build_kleidiai() { build_one kleidiai; }
build_vulkan() { build_one vulkan; }
build_opencl() { build_one opencl; }
build_o3lto() { build_one o3lto; }
if [[ $# == 0 ]]; then set -- omp armv86 kleidiai vulkan opencl o3lto; fi
status=0
for variant in "$@"; do
    case "$variant" in
        omp|armv86|armv82|kleidiai|vulkan|opencl|o3lto) "build_$variant" || status=1 ;;
        *) printf 'Unknown variant: %s\n' "$variant" >&2; status=1 ;;
    esac
done
exit "$status"
