"""Real input shapes for the shadow network's smallest model: token lengths of the
texts it actually sees (next-tool contexts from shadow-learning traces)."""
import json, os, numpy as np
from transformers import AutoTokenizer

SNAP = "/mnt/zer0models/z0-wt/hf-cache/hub/models--unsloth--functiongemma-270m-it/snapshots"
MODEL_DIR = os.path.join(SNAP, os.listdir(SNAP)[0])
DATA = "/mnt/zer0models/zer0-tier/experiments/autoresearch-fly/data/next_tool.jsonl"
tok = AutoTokenizer.from_pretrained(MODEL_DIR)
L = [len(tok(json.loads(l)["text"])["input_ids"]) for l in open(DATA)]
print("n", len(L), "tokens p50/p90/p99/max", *np.percentile(L, [50, 90, 99, 100]).astype(int))
print("MODEL_DIR", MODEL_DIR)
