#!/usr/bin/env python3
"""Predicted naturalness for benchmark WAVs, kept apart from quality.py's PESQ proxy.

UTMOS22 strong (SpeechMOS, tarepan/SpeechMOS v1.2.0, VoiceMOS 2022 winner) predicts a
synthetic-speech naturalness MOS on a 1-5 scale. It is a *prediction*, not a listening
test: every number this writes is labelled `predicted_mos_utmos22`. It does not feed the
bench_s25.py gates (that would be a new epoch); rows go to state/naturalness.tsv.

  python3 mobile/naturalness.py --wav-dir state/runs/<run>/wav --label tts/phonon:phonon-base --out OUT.json
"""
import argparse, datetime, json, pathlib, statistics, sys, wave

import torch

HUB = "tarepan/SpeechMOS:v1.2.0"
MODEL = "utmos22_strong"


def read_wav(path):
    with wave.open(str(path)) as w:
        sr, ch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        raw = w.readframes(n)
    if sw != 2:
        raise SystemExit(f"{path}: only PCM16 supported, got {8 * sw}-bit")
    x = torch.frombuffer(bytearray(raw), dtype=torch.int16).float() / 32768.0
    return x.view(-1, ch).mean(1), sr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav-dir", required=True, type=pathlib.Path)
    ap.add_argument("--label", required=True, help="track:candidate id the WAVs came from")
    ap.add_argument("--out", required=True, type=pathlib.Path)
    ap.add_argument("--tsv", type=pathlib.Path, help="append one summary row here")
    ap.add_argument("--threads", type=int, default=2)
    a = ap.parse_args()
    torch.set_num_threads(a.threads)
    torch.manual_seed(0)
    model = torch.hub.load(HUB, MODEL, trust_repo=True).eval()
    rows = []
    for f in sorted(a.wav_dir.glob("*.wav")):
        x, sr = read_wav(f)
        if x.numel() < sr // 4:
            raise SystemExit(f"{f}: shorter than 0.25 s")
        with torch.inference_mode():
            s = float(model(x.unsqueeze(0), sr).item())
        rows.append({"file": f.name, "predicted_mos_utmos22": round(s, 4), "dur_s": round(x.numel() / sr, 3)})
    if not rows:
        raise SystemExit(f"no WAVs in {a.wav_dir}")
    v = [r["predicted_mos_utmos22"] for r in rows]
    out = {"label": a.label, "model": f"{HUB}/{MODEL}", "kind": "predicted naturalness MOS, not human",
           "n": len(v), "mean": round(statistics.mean(v), 4), "median": round(statistics.median(v), 4),
           "min": min(v), "max": max(v), "per_file": rows, "torch": torch.__version__,
           "at": datetime.datetime.now().isoformat(timespec="seconds")}
    a.out.write_text(json.dumps(out, indent=1))
    if a.tsv:
        with open(a.tsv, "a") as t:
            t.write("\t".join(map(str, (out["at"], "naturalness", *a.label.split(":", 1), "PREDICTED_UTMOS22",
                                        out["mean"], out["median"], out["n"], a.wav_dir.parent.name))) + "\n")
    print(f"METRIC {a.label}.predicted_mos_utmos22 mean={out['mean']} median={out['median']} n={out['n']}")


if __name__ == "__main__":
    sys.exit(main())
