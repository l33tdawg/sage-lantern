#!/usr/bin/env python3
"""Merge the selected local LoRA and export 4/8-bit MLX robustness candidates."""
import argparse
import json
from pathlib import Path
from run import digest,write


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model",type=Path,required=True)
    p.add_argument("--adapter",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    write(args.output/"registration.json",{"base_files":{p.name:digest(p) for p in sorted(args.model.glob("*.safetensors"))},
        "adapter_sha256":digest(args.adapter/"adapters.safetensors"),"source_sha256":digest(Path(__file__)),
        "candidates":[{"bits":4,"group_size":64},{"bits":8,"group_size":64}],
        "scope":"MLX weight-only robustness, not RKLLM W8A8 or board acceptance"})
    import mlx.core as mx
    from mlx.utils import tree_unflatten
    from mlx_lm.utils import load,save
    from mlx_lm.convert import convert
    mx.set_memory_limit(24*1024**3)
    model,tok,config=load(str(args.model),adapter_path=str(args.adapter),return_config=True,tokenizer_config={"trust_remote_code":False})
    fused=[(n,m.fuse()) for n,m in model.named_modules() if hasattr(m,"fuse")]
    model.update_modules(tree_unflatten(fused))
    save(args.output/"fused",str(args.model),model,tok,config,donate_model=False)
    del model
    mx.clear_cache()
    for bits in (4,8):
        path=args.output/f"q{bits}"
        convert(str(args.output/"fused"),str(path),quantize=True,q_bits=bits,q_group_size=64,trust_remote_code=False)
        write(args.output/f"q{bits}-files.json",{"bytes":sum(p.stat().st_size for p in path.glob("*.safetensors")),
            "files":{p.name:digest(p) for p in path.glob("*.safetensors")}})
    print("EXPORTS COMPLETE",args.output)


if __name__=="__main__":main()
