"""Download public BGE weights and pin immutable repository revisions in YAML."""

import json
from pathlib import Path

import yaml
from huggingface_hub import HfApi, snapshot_download


def main():
    records = []
    for config_name, key, repo in [
        ("ingest", "embedding", "BAAI/bge-large-zh-v1.5"),
        ("retrieval", "reranker", "BAAI/bge-reranker-base"),
    ]:
        config_path = Path("configs") / f"{config_name}.yaml"
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        model = config[key]
        info = HfApi().model_info(repo, revision=model["revision"] or "main")
        names = [s.rfilename for s in info.siblings]
        weights = "*.safetensors" if any(n.endswith(".safetensors") for n in names) else "*.bin"
        snapshot_download(
            repo,
            revision=info.sha,
            local_dir=model["model"],
            allow_patterns=["*.json", "*.txt", "*.model", weights],
            ignore_patterns=["onnx/*", "openvino/*"],
        )
        model["revision"] = info.sha
        config_path.write_text(
            yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        records.append({"repo": repo, "revision": info.sha, "path": model["model"]})
        print(json.dumps(records[-1], ensure_ascii=False), flush=True)
    Path("models/manifest.json").write_text(json.dumps(records, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
