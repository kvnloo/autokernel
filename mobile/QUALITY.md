# Speech quality gate

`quality.py` implements the `bench_s25.py` quality interface; it reads the requested slots' WAVs and writes JSON only on success. It is independent of adb. Install into the designated environment (do not replace its torch):

```sh
env -u PYTHONPATH -u PYTHONHOME UV_CACHE_DIR=/mnt/zer0models/models/uv-cache \
  uv pip install --python /mnt/zer0models/models/zlv-tts/.venv/bin/python pytest jiwer
# Match the pre-existing torch 2.11.0+cu128 build; the PyPI default wheel is CUDA 13.
env -u PYTHONPATH -u PYTHONHOME UV_CACHE_DIR=/mnt/zer0models/models/uv-cache \
  uv pip install --python /mnt/zer0models/models/zlv-tts/.venv/bin/python --no-deps \
  'torchaudio==2.11.0+cu128' --index-url https://download.pytorch.org/whl/cu128
```

The environment also needs torch, transformers 5.15.1, numpy, soundfile, librosa, and their dependencies. First execution downloads model weights. The default HF cache is `/mnt/zer0models/models/hf`; torch's asset cache defaults to its `torch` subdirectory. Existing HF_HOME and TORCH_HOME values override these defaults.

```sh
env -u PYTHONPATH -u PYTHONHOME /mnt/zer0models/models/zlv-tts/.venv/bin/python \
  mobile/quality.py --wav-dir DIR --evalset mobile/evalset/v1.json \
  --lane tts --slots inc,cand --out OUT.json
env -u PYTHONPATH -u PYTHONHOME /mnt/zer0models/models/zlv-tts/.venv/bin/python -m pytest -q mobile/tests
```

Whisper `openai/whisper-large-v3-turbo`, revision `41f01f3fe87f28c78e2fbf8b568835947dd65ed9`, runs greedy English transcription. CUDA fp16 is selected only when at least 3 GiB is reported free; otherwise inference is CPU fp32. Actual allocation requirements can exceed 3 GiB, so an allocation failure is fatal, not a silent device change. Each score is calculated after Whisper English text normalization (including written/spoken numbers); jiwer 4.0.0 computes per-file WER/CER, averaged by file. Empty references follow jiwer's empty-reference behavior. Audio over 30 seconds is rejected rather than silently truncated. ASR can hallucinate on silence/noise, misrecognize proper nouns, or prefer paraphrases; WER is not a semantic correctness metric.

The `mos` field is **not human MOS**: it is the no-reference *predicted PESQ* head from torchaudio 2.11.0 SQUIM_OBJECTIVE, checkpoint `squim_objective_dns2020.pth`, 16 kHz mono. The model was trained on DNS2020 speech-enhancement data; PESQ primarily estimates speech transmission/degradation, not voice naturalness, expressiveness, or synthetic-speech preference. Its nominal PESQ-like scale is not directly comparable to human MOS or other MOS models, can extrapolate outside the nominal range, and may systematically favor or penalize particular TTS voices. It can fail on out-of-distribution audio; use paired same-model comparisons plus listening and independent human validation before relying on the benchmark's 0.10 gate. This choice is explicitly a proxy, not a calibrated MOS measurement. Signals shorter than one second are zero-padded for SQUIM. Resampling uses torchaudio's default bandlimited resampler.

For s2s, the reference is each file's adjacent model-generated `.txt`, not the input question. `task_acc` requires all `must_contain` strings to occur case-insensitively in either the complete text answer or the complete audio transcript; an empty list passes. WER/CER test speech/text consistency, not factual correctness.

Audio statistics use original-rate samples: `dur_s` is duration, `silence_ratio` is the fraction of leading and trailing mono samples at absolute amplitude <= 0.01 (all silent means 1), and `clipped_frac` is the fraction of samples across all original channels at absolute amplitude >= 0.999. Multi-channel audio is averaged for inference. These thresholds are diagnostics, not voice activity detection.

Execution sets deterministic PyTorch algorithms, fixed seed, and disables cuDNN benchmarking; no sampling is used. Identical-input reproducibility is tested on the same hardware/software. Results may differ across CPU/GPU, library versions, or hardware; rebaseline rather than comparing different setups. GPU models are released and the CUDA cache emptied at completion. `bench_s25.py` itself treats evaluator errors as provisional rather than fail-closed; this file does not change that behavior. Inputs are assumed to be benchmark-produced; an empty slot, unknown named item, missing s2s text, invalid waveform, non-finite output, or model error exits nonzero. Tests use existing local S25 WAVs and require the models and CUDA/CPU resources; they are not hermetic.
