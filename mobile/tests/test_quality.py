"""Integration tests run the actual installed Whisper and SQUIM models."""
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
import soundfile as sf

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "mobile/quality.py"
EVALSET = ROOT / "mobile/evalset/v1.json"
DATA = Path("/mnt/zer0models/models/zlv-tts/acpp-out/s25")


def run_quality(wav_dir, lane, slots, out):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    return subprocess.run([sys.executable, str(SCRIPT), "--wav-dir", str(wav_dir),
                           "--evalset", str(EVALSET), "--lane", lane, "--slots", slots,
                           "--out", str(out)], env=env, text=True, capture_output=True, timeout=600)


@pytest.fixture
def wavs(tmp_path):
    for target, source in [("inc-ack-r0.wav", "phonon-ack-1.wav"),
                           ("cand-ack-r0.wav", "pocket-ack-2.wav")]:
        (tmp_path / target).symlink_to(DATA / source)
    audio, sr = sf.read(DATA / "phonon-ack-1.wav")
    noise = np.random.default_rng(7).normal(0, .15, len(audio))
    sf.write(tmp_path / "noise-ack-r0.wav", noise, sr)
    return tmp_path


def test_tts_real_deterministic_and_noise(wavs):
    results = []
    for i in range(2):
        out = wavs / f"quality{i}.json"
        proc = run_quality(wavs, "tts", "inc,cand,noise", out)
        assert proc.returncode == 0, proc.stderr
        results.append(json.loads(out.read_text()))
    assert results[0] == results[1]
    result = results[0]
    assert result["status"] == "ok"
    inc, cand, noise = (result["slots"][s] for s in ("inc", "cand", "noise"))
    assert inc["n"] == cand["n"] == noise["n"] == 1
    assert noise["wer"] > inc["wer"] + 0.4
    assert noise["mos"] < inc["mos"] - 0.5
    assert noise["mos"] < cand["mos"] - 0.5
    for slot in (inc, cand, noise):
        f = slot["per_file"][0]
        assert f["dur_s"] > 0 and 0 <= f["silence_ratio"] <= 1 and 0 <= f["clipped_frac"] <= 1


def test_s2s_real(wavs, tmp_path):
    # Separate directory: ack WAVs are intentionally not s2s evalset items.
    d = tmp_path / "s2s"
    d.mkdir()
    for ext in ("wav", "txt"):
        (d / f"inc-capital-r0.{ext}").symlink_to(DATA / f"lfm3-capital.{ext}")
    out = d / "quality.json"
    proc = run_quality(d, "s2s", "inc", out)
    assert proc.returncode == 0, proc.stderr
    slot = json.loads(out.read_text())["slots"]["inc"]
    assert slot["n"] == 1 and slot["task_acc"] == 1
    assert "canberra" in slot["per_file"][0]["ref"].lower()
    assert slot["per_file"][0]["wer"] < .6


def test_fails_on_missing_slot(tmp_path):
    out = tmp_path / "out.json"
    proc = run_quality(tmp_path, "tts", "inc", out)
    assert proc.returncode != 0 and "no WAV files" in proc.stderr
    assert not out.exists()


def test_whisper_normalizes_numbers(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    proc = subprocess.run([sys.executable, "-c", "from transformers.models.whisper.english_normalizer import EnglishTextNormalizer; n=EnglishTextNormalizer({}); assert n('seventy two') == n('72')"], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
