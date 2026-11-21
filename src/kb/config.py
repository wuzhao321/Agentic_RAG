from pathlib import Path

import yaml


def load_config(name: str, root: Path = Path(".")) -> dict:
    path = root / "configs" / f"{name}.yaml"
    with path.open(encoding="utf-8") as f:
        config = yaml.safe_load(f)
    if not isinstance(config, dict):
        raise ValueError(f"配置必须是映射: {path}")
    return config


def resolve_model_path(config: dict, root: Path) -> dict:
    """Resolve a local model for loading while retaining its configured identity on disk."""
    return {**config, "model": str((root / config["model"]).resolve())}
