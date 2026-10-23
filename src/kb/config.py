from pathlib import Path

import yaml


def load_config(name: str, root: Path = Path(".")) -> dict:
    path = root / "configs" / f"{name}.yaml"
    with path.open(encoding="utf-8") as f:
        config = yaml.safe_load(f)
    if not isinstance(config, dict):
        raise ValueError(f"配置必须是映射: {path}")
    return config
