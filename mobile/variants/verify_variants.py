#!/usr/bin/env python3
"""Read back and verify all six real artifacts; optionally retain build receipts."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

NAMES = ("omp", "armv86", "kleidiai", "vulkan", "opencl", "o3lto")
STOCK = {"libc.so", "libm.so", "libdl.so", "liblog.so", "libvulkan.so"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/mnt/zer0models/models/zlv-tts/phone-bundle/variants"))
    parser.add_argument("--readelf", default="/home/kvn/android/sdk/ndk/30.0.16248370/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-readelf")
    parser.add_argument("--receipts-dir", type=Path)
    args = parser.parse_args()
    records = []
    for name in NAMES:
        directory = args.root / name
        record = json.loads((directory / "BUILD.json").read_text())
        assert record["variant"] == name, (name, "wrong receipt variant")
        assert isinstance(record["built"], bool), (name, "missing build result")
        assert record["reason"], (name, "missing reason")
        assert record["compiler_version"], (name, "missing compiler version")
        assert isinstance(record["build_seconds"], (int, float))
        binary = directory / "audiocpp_cli"
        if record["built"]:
            assert record["cmake_command"] and record["flags"]
            data = binary.read_bytes()
            assert hashlib.sha256(data).hexdigest() == record["sha256"], (name, "hash mismatch")
            assert len(data) == record["size_bytes"], (name, "size mismatch")
            desc = subprocess.check_output(["file", str(binary)], text=True)
            assert "ELF 64-bit" in desc and "ARM aarch64" in desc and ", stripped" in desc, desc
            dynamic = subprocess.check_output([args.readelf, "-d", str(binary)], text=True)
            needed = re.findall(r"\(NEEDED\).*?\[(.*?)\]", dynamic)
            assert needed == record["needed"], (name, "NEEDED mismatch")
            assert not set(needed) - STOCK, (name, "non-stock dependencies", needed)
            assert "libvulkan.so" not in needed or name == "vulkan"
        else:
            assert not binary.exists(), (name, "stale failed-build binary")
        if args.receipts_dir:
            args.receipts_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(directory / "BUILD.json", args.receipts_dir / (name + ".json"))
        records.append({"name": name, **{key: record.get(key) for key in
                        ("built", "size_mb", "sha256", "needed", "notes")}})
    assert len(records) == len(NAMES)
    print(json.dumps({"verified_count": len(records), "variants": records}, indent=2))


if __name__ == "__main__":
    main()
