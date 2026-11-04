"""Queue of program.md experiments for one kernel, applied one at a time on top of the
current best kernel.py through z0/exp.sh (commit, bench, keep or git reset).
Each variant edits the launch config literals in kernel.py.
  python3 z0/queue.py workspace/kernel_matmul_1.py"""
import re, subprocess, sys

FILE = sys.argv[1]
VARIANTS = [  # (description, {literal: value})
    ("BLOCK_N 256 (128x256x64), wide N=262144", {"BLOCK_SIZE_N": "256"}),
    ("num_stages 4", {"num_stages": "4"}),
    ("BLOCK_K 32, num_stages 4", {"BLOCK_SIZE_K": "32 if A.dtype != torch.float32 else 32", "num_stages": "4"}),
    ("BLOCK_M 256 (256x128)", {"BLOCK_SIZE_M": "256"}),
    ("num_warps 4", {"num_warps": "4"}),
    ("GROUP_M 16", {"GROUP_M_LAUNCH": "16"}),
]


def apply(src, changes):
    for key, val in changes.items():
        if key == "GROUP_M_LAUNCH":
            if "GROUP_M=" in src:
                src = re.sub(r"GROUP_M=\d+,", f"GROUP_M={val},", src)
            else:
                src = src.replace("        num_warps=", f"        GROUP_M={val},\n        num_warps=", 1)
        elif key in ("num_stages", "num_warps"):
            src = re.sub(rf"{key}=\d+", f"{key}={val}", src, count=1)
        else:
            src, n = re.subn(rf"(    {key} = )[^\n]+", rf"\g<1>{val}", src, count=1)
            assert n == 1, key
    return src


for desc, ch in VARIANTS:
    src = open("kernel.py").read()
    new = apply(src, ch)
    if new == src:
        print(f"skip (no change): {desc}", flush=True); continue
    open("kernel.py", "w").write(new)
    print(f"\n=== {desc}", flush=True)
    r = subprocess.run(["bash", "z0/exp.sh", FILE, desc], capture_output=True, text=True)
    print(r.stdout[-800:], r.stderr[-400:], flush=True)
    subprocess.run(["git", "checkout", "-q", "--", "kernel.py"])  # leave tree at HEAD (best)
