"""s2s time-to-first-audio seam: LFM3's 7.4 s was cold START->END wall (proposals/voice-naturalness-latency-20261105.md 2).

The ttfa track needs a LOADED marker (`runtime.load.phase loaded`, --load-progress 1) and a FIRST marker (the first
`stream_audio` line from variants/patches/streammark.patch). The existing s2s/lfm3 wall track must keep its exact
epoch-4 command. The shell test runs the real phone_block() script under sh with a fake CLI that prints what the
patched audiocpp_cli prints, with known gaps, so the measured load->first time is checked end to end on the host.
"""
import json
import os
import pathlib
import stat
import subprocess
import sys

MOBILE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MOBILE))
import bench_s25 as b  # noqa: E402

# cmd line 3 of state/runs/20261105-000921-base-lfm3-base/only-r0.sh (the epoch-4 lfm3-base baseline on the S25)
EPOCH4_LFM3 = ("timeout 120 /data/local/tmp/z0live-ar/bin/base/audiocpp_cli --task s2s --family lfm3_audio --model "
               "/data/local/tmp/z0live-ar/m/LFM3-Audio-2B-GGUF/LFM3-Audio-2B-Q4_0.gguf --backend cpu --threads 4 --mode "
               "streaming --out /data/local/tmp/z0live-ar/out/only-capital-r0.wav --log --audio "
               "/data/local/tmp/z0live-ar/m/ref/q-capital.wav --text-out /data/local/tmp/z0live-ar/out/only-capital-r0.txt "
               "--model-spec-override /data/local/tmp/z0live-ar/m/specs/lfm3_audio.json")


def cand(name):
    c = json.loads((MOBILE / "candidates" / f"{name}.json").read_text())
    c.setdefault("objective", "wall")
    return c


def item(i):
    return next(x for x in b.EVALSET["s2s"] if x["id"] == i)


def test_wall_track_command_is_byte_identical_to_epoch4():
    c = cand("lfm3-base")
    assert not b.is_ttfa(c)
    assert b.cmdline(c, item("capital"), f"{b.PD}/out/only-capital-r0.wav") == EPOCH4_LFM3


def test_ttfa_track_is_its_own_track_with_load_marker():
    c, base = cand("lfm3-ttfa-base"), cand("lfm3-base")
    assert b.is_ttfa(c) and c["objective"] == "ttfa_warm" and c["track"] != base["track"]
    cmd = b.cmdline(c, item("capital"), f"{b.PD}/out/x.wav")
    assert cmd.endswith("--load-progress 1") and "/bin/streammark/audiocpp_cli" in cmd


def test_warm_block_primes_once_and_prime_is_never_a_record():
    c = dict(cand("lfm3-ttfa-base"), _slot="only")
    items = b.EVALSET["s2s"]
    jobs = b.block_jobs(c, items, 1)
    assert [t for t, _, _ in jobs] == ["only-warm-r1"] + [f"only-{i['id']}-r1" for i in items]
    cold = b.block_jobs(dict(cand("lfm3-base"), _slot="only"), items, 1)
    assert [t for t, _, _ in cold] == [f"only-{i['id']}-r1" for i in items]


FAKE_CLI = """#!/bin/sh
# what the streammark audiocpp_cli prints for lfm3_audio s2s --mode streaming --log --load-progress 1
echo "[TRACE ts=1.0] runtime.load.phase inspect" >&2
echo "[TRACE ts=1.1] runtime.load.phase load" >&2
sleep 0.3
echo "[TRACE ts=1.4] runtime.load.phase loaded" >&2
echo "[TIMING] lfm3_audio.encoder.ms 40.0" >&2
sleep 0.4
echo "partial_text=The capital"
%s
sleep 0.2
%s
echo "family=lfm3_audio"
echo "audio_out=$OUT"
printf 'RIFF....' > "$OUT"
"""


def run_block(tmp_path, chunk_line):
    cli = tmp_path / "cli"
    cli.write_text(FAKE_CLI % (chunk_line, chunk_line))
    cli.chmod(cli.stat().st_mode | stat.S_IEXEC)
    wav = tmp_path / "a.wav"
    script = b.phone_block([("only-capital-r0", f"OUT={wav} {cli}", str(wav))])
    out = subprocess.run(["sh", "-c", script], capture_output=True, text=True, timeout=30, env={**os.environ}).stdout
    return b.parse_events(out)["only-capital-r0"]


def test_shell_block_measures_load_to_first_audio(tmp_path):
    e = run_block(tmp_path, 'echo "stream_audio samples=1920 sample_rate=24000"')
    assert e["start"] < e["loaded"] < e["first"] < e["end"] and not e["errs"]
    load_to_first_ms = (e["first"] - e["loaded"]) / 1e6
    start_to_first_ms = (e["first"] - e["start"]) / 1e6
    assert 380 <= load_to_first_ms < 1500, load_to_first_ms  # 0.4 s encode/prefill sleep, not the 0.2 s 2nd chunk
    assert start_to_first_ms - load_to_first_ms >= 280  # the 0.3 s load is excluded from the warm number


def test_base_binary_reports_no_first_instead_of_a_fake_one(tmp_path):
    e = run_block(tmp_path, ":")  # epoch-4 base binary: no per-chunk line
    assert "loaded" in e and "first" not in e


def test_ttfa_warm_objective_reads_load_to_first():
    rs = [{"errs": [], "audio_s": 4.0, "contaminated": False, "load_to_first_ms": v, "proc_ms": 7400.0} for v in (410, 430, 450)]
    assert b.summarize(rs, "ttfa_warm")["objective"] == 430
    assert b.summarize(rs, "wall")["objective"] == 7400.0


def test_supervisor_skips_a_candidate_until_its_variant_is_built(tmp_path, monkeypatch):
    import fulltime as f
    monkeypatch.setattr(f, "STATE", tmp_path)
    monkeypatch.setattr(f, "RECEIPTS", tmp_path / "fulltime.jsonl")
    monkeypatch.setattr(b, "VARIANTS", tmp_path / "variants")
    (tmp_path / "variants/base").mkdir(parents=True)
    (tmp_path / "variants/base/audiocpp_cli").write_text("")

    def baselines():
        seen = []
        with open(f.RECEIPTS, "a") as r:
            while (nxt := f.next_item()) and nxt[0] == "baseline" and len(seen) < 500:
                seen.append(nxt[-1])
                r.write(json.dumps({"at": "2026-11-05T06:00:00", "kind": "end", "args": nxt, "rc": 0, "wall_s": 1}) + "\n")
                r.flush()
        return seen

    first = baselines()
    assert first and "candidates/lfm3-ttfa-base.json" not in first
    (tmp_path / "variants/streammark").mkdir()
    (tmp_path / "variants/streammark/audiocpp_cli").write_text("")
    assert baselines() == ["candidates/lfm3-ttfa-base.json"]
