#!/usr/bin/env bash
# Fleet-node side of one variant cross-build (never touches a phone). Provisions the pinned NDK r30 + SDK cmake/ninja
# and a pristine audio.cpp a01aed4 clone in the node's persistent HOME cache, then runs build_variants.sh.
# Submit from groot (mobile/variants):
#   fleet-build --android --cpus 2 --mem 4G --timeout 7200 --wait 3600 --src-include mobile/variants \
#     --fetch out -- bash fleet_build.sh streammark
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
CACHE=$HOME/.cache/z0-audiocpp-build
SDK=$CACHE/android-sdk
NDK_VER=30.0.16248370 CMAKE_VER=3.22.1 SHA=a01aed4a15c1da59010e48bc15fb029ecb961975
mkdir -p "$CACHE"
if [[ ! -x $SDK/ndk/$NDK_VER/toolchains/llvm/prebuilt/linux-x86_64/bin/clang || ! -x $SDK/cmake/$CMAKE_VER/bin/ninja ]]; then
    yes | sdkmanager --sdk_root="$SDK" --licenses >/dev/null || true
    sdkmanager --sdk_root="$SDK" "ndk;$NDK_VER" "cmake;$CMAKE_VER"
fi
SRC=$CACHE/audio.cpp
[[ -d $SRC/.git ]] || git clone --quiet https://github.com/0xShug0/audio.cpp "$SRC"
git -C "$SRC" cat-file -e "$SHA^{commit}" 2>/dev/null || git -C "$SRC" fetch --quiet origin
git -C "$SRC" checkout --quiet --force --detach "$SHA"
export PATH=$SDK/cmake/$CMAKE_VER/bin:$PATH
export AUDIOCPP_SOURCE=$SRC ANDROID_NDK=$SDK/ndk/$NDK_VER VARIANTS_OUT=$HERE/out BUILD_RUNNER=fleet
export BUILD_JOBS=${BUILD_JOBS:-2}
bash "$HERE/build_variants.sh" "$@"
