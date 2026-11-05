#!/usr/bin/env python3
"""Lightweight executable-interface checks; never builds or contacts a phone."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent


class BuildInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = os.environ.copy()
        self.env.update(AUDIOCPP_SOURCE=str(self.root / "source"),
                        ANDROID_NDK=str(self.root / "missing-ndk"),
                        VARIANTS_OUT=str(self.root / "out"), BUILD_RUNNER="local")
        self.env.pop("ALLOW_LOCAL_BUILD", None)

    def call(self, *args):
        return subprocess.run(["bash", str(HERE / "build_variants.sh"), *args],
                              env=self.env, capture_output=True, text=True)

    def test_local_build_requires_explicit_admission(self):
        result = self.call("omp")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Use fleet-build", result.stderr)
        self.assertFalse((self.root / "out").exists())

    def test_unknown_variant_is_rejected_without_outputs(self):
        self.env["ALLOW_LOCAL_BUILD"] = "1"
        result = self.call("not-a-variant")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Unknown variant", result.stderr)
        self.assertFalse((self.root / "out").exists())

    def test_missing_toolchain_records_failure_and_removes_stale_binary(self):
        self.env["ALLOW_LOCAL_BUILD"] = "1"
        dest = self.root / "out/omp"
        dest.mkdir(parents=True)
        (dest / "audiocpp_cli").write_bytes(b"stale-test-artifact")
        result = self.call("omp")
        self.assertEqual(result.returncode, 1)
        record = json.loads((dest / "BUILD.json").read_text())
        self.assertFalse(record["built"])
        self.assertIn("missing-ndk", record["reason"])
        self.assertEqual(record["needed"], [])
        self.assertFalse((dest / "audiocpp_cli").exists())
        self.assertGreaterEqual(record["build_seconds"], 0)

    def test_batch_continues_after_failed_variant(self):
        self.env["ALLOW_LOCAL_BUILD"] = "1"
        result = self.call("omp", "armv86", "streammark")
        self.assertEqual(result.returncode, 1)
        for name in ("omp", "armv86", "streammark"):
            data = json.loads((self.root / "out" / name / "BUILD.json").read_text())
            self.assertFalse(data["built"])
            self.assertEqual(data["variant"], name)


if __name__ == "__main__":
    unittest.main()
