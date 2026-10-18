#!/usr/bin/env python3
"""One fixed small LoRA trial. No evaluation cases or personal SAGE data loaded."""
import argparse
import json
import math
import time
from pathlib import Path
from run import digest, write


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--model", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    manifest = json.loads((args.data / "manifest.json").read_text())
    if digest(args.data / "train.jsonl") != manifest["train_sha256"]:
        raise ValueError("training_data_changed")
    args.output.mkdir(parents=True, exist_ok=False)
    config = manifest["parameters"]
    registration = {"training_manifest_sha256": digest(args.data / "manifest.json"),
        "source_sha256": digest(Path(__file__)), "parameters": config,
        "base_model_files": {p.name: digest(p) for p in sorted(args.model.glob("*.safetensors"))},
        "evaluation_data_loaded": False, "status": "registered_before_training"}
    write(args.output / "registration.json", registration)
    import mlx.core as mx
    import mlx.nn as nn
    import mlx.optimizers as optim
    import numpy as np
    from mlx_lm import load
    from mlx_lm.tuner.utils import linear_to_lora_layers
    from mlx_lm.tuner.trainer import train, TrainingArgs, TrainingCallback
    mx.set_memory_limit(24 * 1024 ** 3)
    mx.set_cache_limit(512 * 1024 ** 2)
    mx.random.seed(config["seed"])
    model, tokenizer = load(str(args.model), tokenizer_config={"trust_remote_code": False})
    model.freeze()
    lora = {"rank": config["rank"], "scale": float(config["rank"]), "dropout": 0.0, "keys": config["keys"]}
    linear_to_lora_layers(model, config["layers"], lora)
    write(args.output / "adapter_config.json", {"model": str(args.model.resolve()), "fine_tune_type": "lora",
                                                "num_layers": config["layers"], "lora_parameters": lora})
    data = []
    for line in (args.data / "train.jsonl").read_text().splitlines():
        row = json.loads(line)
        tokens = tokenizer.apply_chat_template(row["messages"], tools=row["tools"], tokenize=True)
        prefix = tokenizer.apply_chat_template(row["messages"][:-1], tools=row["tools"], tokenize=True,
                                               add_generation_prompt=True)
        if tokens[:len(prefix)] != prefix or not 0 < len(prefix) < len(tokens) <= 1024:
            raise ValueError("training_inference_boundary_or_length: " + row["id"])
        data.append((tokens, len(prefix), row["id"]))
    if len(data) != manifest["rows"]:
        raise ValueError("training_rows_changed")
    write(args.output / "token_audit.json", {"rows": len(data), "maximum_tokens": max(len(d[0]) for d in data),
        "all_inference_prefixes_exact": True, "mask": "target position >= completion offset and < exclusive sequence length",
        "trainable_parameters": sum(p.size for _, p in __import__('mlx.utils', fromlist=['tree_flatten']).tree_flatten(model.trainable_parameters()))})
    def loss(model, batch, lengths):
        targets = batch[:, 1:]
        logits = model(batch[:, :-1])
        positions = mx.arange(1, targets.shape[1] + 1)
        mask = (positions >= lengths[:, 0:1]) & (positions < lengths[:, 1:])
        ce = nn.losses.cross_entropy(logits, targets).astype(mx.float32)
        count = mask.sum()
        return mx.where(mask, ce, 0).sum() / count, count
    rng = np.random.default_rng(config["seed"])
    order = []
    for _ in range(config["epochs"]):
        permutation = rng.permutation(len(data)).tolist()
        order.extend(permutation[i:i+config["batch_size"]] for i in range(0, len(data), config["batch_size"]))
    def batches(dataset, batch_size, max_seq_length, **kwargs):
        if dataset is not data or batch_size != config["batch_size"]:
            raise ValueError("wrong_training_batch_contract")
        for indices in order:
            items = [data[i] for i in indices]
            size = 32 * math.ceil(max(len(x[0]) for x in items) / 32)
            arr = np.zeros((len(items), size), dtype=np.int32)
            for i, item in enumerate(items):
                arr[i, :len(item[0])] = item[0]
            yield mx.array(arr), mx.array([[x[1], len(x[0])] for x in items])
    class Callback(TrainingCallback):
        def on_train_loss_report(self, info):
            if not np.isfinite(info["train_loss"]):
                raise ValueError("nonfinite_loss")
            with (args.output / "metrics.jsonl").open("a") as handle:
                handle.write(json.dumps(info) + "\n")
    start = time.perf_counter()
    train(model, optim.Adam(learning_rate=config["learning_rate"]), data, None,
          args=TrainingArgs(batch_size=config["batch_size"], iters=len(order), max_seq_length=1024,
                            steps_per_report=20, steps_per_eval=len(order)+1, steps_per_save=len(order),
                            adapter_file=str(args.output / "adapters.safetensors"), clear_cache_threshold=512*1024**2),
          loss=loss, iterate_batches=batches, training_callback=Callback())
    write(args.output / "complete.json", {"updates": len(order), "seconds": time.perf_counter()-start,
        "peak_mlx_gb": mx.get_peak_memory()/1024**3,
        "adapter_sha256": digest(args.output / "adapters.safetensors"),
        "qualification": "lab-only; no evaluation or deployment qualification from training loss"})
    print((args.output / "complete.json").read_text())


if __name__ == "__main__":
    main()
