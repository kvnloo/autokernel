#!/usr/bin/env python3
"""Offline quality measurements for the frozen S25 benchmark; see QUALITY.md."""
import argparse
import gc
import json
import os
from pathlib import Path
import re
import statistics
import sys

# The checkout contains profile.py, which shadows the standard library module
# when this script or pytest is run from the repository root.
_root = str(Path(__file__).resolve().parent.parent)
sys.path[:] = [p for p in sys.path if p and str(Path(p).resolve()) != _root]
os.environ.setdefault("HF_HOME", "/mnt/zer0models/models/hf")
os.environ.setdefault("TORCH_HOME", "/mnt/zer0models/models/hf/torch")
os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")

import jiwer
import numpy as np
import soundfile as sf
import torch
import torchaudio
from transformers import WhisperForConditionalGeneration, WhisperProcessor
from transformers.models.whisper.english_normalizer import EnglishTextNormalizer

ASR = "openai/whisper-large-v3-turbo"
REVISION = "41f01f3fe87f28c78e2fbf8b568835947dd65ed9"
MOS = "torchaudio SQUIM_OBJECTIVE 2.11.0 (PESQ proxy)"


def read_audio(path):
    x, rate = sf.read(path, dtype="float32", always_2d=True)
    if not len(x) or rate <= 0 or not np.isfinite(x).all():
        raise ValueError(f"empty or invalid WAV: {path}")
    mono = x.mean(axis=1)
    active = np.flatnonzero(np.abs(mono) > 0.01)
    silence = 1.0 if not len(active) else (active[0] + len(mono) - 1 - active[-1]) / len(mono)
    return mono, rate, {"dur_s": len(mono) / rate, "silence_ratio": float(silence),
                        "clipped_frac": float(np.mean(np.abs(x) >= 0.999))}


def collect(wav_dir, evalset, lane, slots):
    items = {item["id"]: item for item in evalset[lane]}
    if len(items) != len(evalset[lane]):
        raise ValueError("duplicate evalset item id")
    results = []
    for slot in slots:
        found = 0
        for path in sorted(wav_dir.glob(f"{slot}-*-r*.wav")):
            match = re.fullmatch(re.escape(slot) + r"-(.+)-r(\d+)\.wav", path.name)
            if not match or match[1] not in items:
                raise ValueError(f"unknown or malformed WAV name: {path.name}")
            item = items[match[1]]
            if lane == "s2s":
                ref = path.with_suffix(".txt").read_text(encoding="utf-8").strip()
            else:
                ref = item["text"]
            audio, rate, metrics = read_audio(path)
            if len(audio) / rate > 30:
                raise ValueError(f"WAV exceeds Whisper's 30-second limit: {path}")
            results.append(dict(slot=slot, path=path, item=item, ref=ref, audio=audio, rate=rate, metrics=metrics))
            found += 1
        if not found:
            raise ValueError(f"no WAV files for slot {slot}")
    return results


def score(records):
    torch.set_num_threads(4)
    torch.manual_seed(0)
    torch.use_deterministic_algorithms(True)
    torch.backends.cudnn.benchmark = False
    device = "cuda" if torch.cuda.is_available() and torch.cuda.mem_get_info()[0] >= 3 * 1024**3 else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32
    model = None
    try:
        squim = torchaudio.pipelines.SQUIM_OBJECTIVE.get_model().to(device).eval()
        with torch.inference_mode():
            for r in records:
                x = torch.from_numpy(r["audio"]).unsqueeze(0)
                if r["rate"] != 16000:
                    x = torchaudio.functional.resample(x, r["rate"], 16000)
                r["resampled"] = x.squeeze(0).numpy()
                # SQUIM requires sufficient signal for its encoder; padding short inputs
                # is preferable to failing the whole benchmark on a broken output.
                if x.shape[-1] < 16000:
                    x = torch.nn.functional.pad(x, (0, 16000 - x.shape[-1]))
                r["mos"] = float(squim(x.to(device))[1].item())
        del squim
        gc.collect()
        if device == "cuda":
            torch.cuda.empty_cache()
        processor = WhisperProcessor.from_pretrained(ASR, revision=REVISION)
        model = WhisperForConditionalGeneration.from_pretrained(ASR, revision=REVISION, dtype=dtype, attn_implementation="eager").to(device).eval()
        with torch.inference_mode():
            for r in records:
                inputs = processor(r.pop("resampled"), sampling_rate=16000, return_tensors="pt", return_attention_mask=True)
                features = inputs.input_features.to(device=device, dtype=dtype)
                mask = inputs.attention_mask.to(device)
                ids = model.generate(features, attention_mask=mask, language="english", task="transcribe", do_sample=False, num_beams=1, max_new_tokens=225)
                r["hyp"] = processor.batch_decode(ids, skip_special_tokens=True)[0]
    finally:
        del model
        gc.collect()
        if device == "cuda":
            torch.cuda.empty_cache()


def evaluate(wav_dir, evalset, lane, slots):
    records = collect(wav_dir, evalset, lane, slots)
    score(records)
    normalize = EnglishTextNormalizer({})
    output = {"status": "ok", "asr_model": ASR + "@" + REVISION, "mos_model": MOS, "slots": {}}
    for slot in slots:
        files = []
        task = []
        for r in records:
            if r["slot"] != slot:
                continue
            ref, hyp = normalize(r["ref"]), normalize(r["hyp"])
            wer = jiwer.wer(ref, hyp)
            cer = jiwer.cer(ref, hyp)
            f = {"file": r["path"].name, "item": r["item"]["id"], "ref": r["ref"], "hyp": r["hyp"],
                 "wer": wer, "cer": cer, "mos": r["mos"], **r["metrics"]}
            if not all(np.isfinite(f[k]) for k in ("wer", "cer", "mos", "dur_s", "silence_ratio", "clipped_frac")):
                raise ValueError(f"non-finite score for {r['path']}")
            files.append(f)
            if lane == "s2s":
                terms = r["item"]["must_contain"]
                # All terms must be in one answer, not split between text and audio.
                task.append(any(all(term.casefold() in answer.casefold() for term in terms)
                                for answer in (r["ref"], r["hyp"])))
        output["slots"][slot] = {k: statistics.mean(f[k] for f in files) for k in ("wer", "cer", "mos")}
        output["slots"][slot].update(n=len(files), per_file=files)
        if lane == "s2s":
            output["slots"][slot]["task_acc"] = statistics.mean(task)
    return output


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--wav-dir", type=Path, required=True)
    p.add_argument("--evalset", type=Path, required=True)
    p.add_argument("--lane", choices=("tts", "s2s"), required=True)
    p.add_argument("--slots", required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    slots = args.slots.split(",")
    if any(not re.fullmatch(r"[A-Za-z0-9_]+", s) for s in slots) or len(set(slots)) != len(slots):
        p.error("slots must be distinct alphanumeric/underscore identifiers")
    try:
        result = evaluate(args.wav_dir, json.loads(args.evalset.read_text()), args.lane, slots)
        args.out.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    except Exception as e:
        print(f"quality: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
