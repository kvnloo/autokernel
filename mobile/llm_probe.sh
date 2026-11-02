#!/usr/bin/env bash
# LLM backend probe on the S25 (CPU vs Adreno OpenCL vs Hexagon HTP v79) with upstream llama.cpp b12053
# android-arm64-snapdragon build. adb shell only; holds the bench lock so it never overlaps bench runs.
set -u
S=${S25_SERIAL:-100.117.226.39:39057}
L=/mnt/zer0models/models/zlv-tts/phone-bundle/llm-variants/snapdragon
M=/mnt/zer0models/models/zlv-tts/acpp-models/llm
PD=/data/local/tmp/z0live-ar/llm
OUT=${OUT:-$(dirname "$0")/state/llm-probe-$(date +%Y%m%d-%H%M%S).log}
exec 9>/tmp/s25-bench.lock; flock 9
a() { adb -s "$S" "$@"; }
a shell "mkdir -p $PD/m"
a shell "[ -f $PD/.pushed-b12053 ]" || { a push -q "$L/bin" "$L/lib" "$PD/" && a shell "chmod 755 $PD/bin/*; touch $PD/.pushed-b12053"; }
for f in Qwen3-0.6B-Q4_0.gguf LFM2-1.2B-Q4_0.gguf; do a shell "[ -f $PD/m/$f ]" || a push -q "$M/$f" "$PD/m/$f"; done
ENV="cd $PD; export LD_LIBRARY_PATH=$PD/lib ADSP_LIBRARY_PATH=$PD/lib"
{
  echo "## devices"; a shell "$ENV; ./bin/llama-cli --list-devices 2>&1 | tail -8"
  for f in Qwen3-0.6B-Q4_0.gguf LFM2-1.2B-Q4_0.gguf; do
    for dev in none GPUOpenCL HTP0; do
      ngl=99; [ $dev = none ] && ngl=0
      for t in 4 6; do
        [ $dev != none ] && [ $t = 6 ] && continue
        echo "## $f dev=$dev t=$t $(a shell 'dumpsys battery | grep -E "^  (level|temperature):" | tr -d "\n "')"
        a shell "$ENV; timeout 300 ./bin/llama-bench -m m/$f -p 64 -n 32 -r 3 -t $t -dev $dev -ngl $ngl -o md 2>&1 | grep -E '^\|' | tail -3"
      done
    done
  done
} 2>&1 | tee "$OUT"
