"""Download the pinned, public MLX Qwen model used by the local demo."""

from huggingface_hub import snapshot_download

from kb.config import load_config

if __name__ == "__main__":
    config = load_config("demo")
    snapshot_download(
        config["model_repository"],
        revision=config["model_revision"],
        local_dir=config["model_path"],
    )
