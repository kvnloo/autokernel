"""Is a bench correctness FAIL a kernel bug or a too-tight tolerance? Compare kernel.py and
torch.matmul to an fp64 ground truth on the failing cases."""
import sys, os, torch
sys.path.insert(0, os.getcwd())
from kernel import kernel_fn

for name, (M, N, K), dt in [("deep_k", (1024, 1024, 8192), torch.bfloat16),
                            ("deep_k", (1024, 1024, 8192), torch.float32),
                            ("edge_1537", (1537, 1537, 1537), torch.float16),
                            ("edge_1537", (1537, 1537, 1537), torch.bfloat16),
                            ("model_mlp", (3072, 2048, 640), torch.bfloat16)]:
    g = torch.Generator(device="cuda").manual_seed(42)
    A = torch.randn(M, K, device="cuda", generator=g).to(dt)
    B = torch.randn(K, N, device="cuda", generator=g).to(dt)
    truth = A.double() @ B.double()
    ek = (kernel_fn(A, B).double() - truth).abs().max().item()
    et = (torch.matmul(A, B).double() - truth).abs().max().item()
    print(f"{name:10s} {str(dt):15s} kernel_vs_fp64={ek:.4g}  torch_vs_fp64={et:.4g}  "
          f"{'TOLERANCE' if ek <= 1.5 * et else 'KERNEL BUG?'}")
