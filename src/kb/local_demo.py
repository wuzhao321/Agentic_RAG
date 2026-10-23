"""Own the local model process for the lifetime of the demo server."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from kb.config import load_config


def run_local_demo():
    from kb.web import serve

    cfg = load_config("demo")
    os.environ["LANGSMITH_TRACING"] = "false"
    if not list(Path(cfg["model_path"]).glob("*.safetensors")):
        raise ValueError("缺少本地模型，请先执行 python scripts/download_demo_model.py")
    if not Path("data/index/active.json").exists():
        raise ValueError("缺少索引，请先准备公开数据并执行 kb ingest")
    for port in [cfg["model_port"], cfg["web_port"]]:
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError as exc:
                raise ValueError(f"本机端口 {port} 不可用，请检查是否已有 Demo 正在运行") from exc
    Path("data/demo").mkdir(parents=True, exist_ok=True)
    cache = Path(".cache/huggingface/hub").resolve()
    cache.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "LANGSMITH_TRACING": "false",
        "HF_HUB_CACHE": str(cache),
    }
    with Path("data/demo/model-server.log").open("a") as log:
        model = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "mlx_lm.server",
                "--model",
                cfg["model_path"],
                "--host",
                "127.0.0.1",
                "--port",
                str(cfg["model_port"]),
                "--allowed-origins",
                f"http://127.0.0.1:{cfg['web_port']}",
                "--decode-concurrency",
                "1",
                "--prompt-concurrency",
                "1",
                "--prompt-cache-size",
                "2",
                "--max-tokens",
                "1200",
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
        )
        try:
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if model.poll() is not None:
                    raise RuntimeError("模型服务启动失败，详见 data/demo/model-server.log")
                try:
                    with urlopen(
                        f"http://127.0.0.1:{cfg['model_port']}/v1/models", timeout=2
                    ) as response:
                        json.load(response)
                    break
                except (URLError, TimeoutError, json.JSONDecodeError):
                    time.sleep(0.5)
            else:
                raise RuntimeError("模型服务启动超时，详见 data/demo/model-server.log")
            print("本地模型已启动；首次问答会加载检索模型。Ctrl+C 关闭所有 demo 服务。", flush=True)
            serve(cfg["web_port"])
        finally:
            model.terminate()
            try:
                model.wait(timeout=10)
            except subprocess.TimeoutExpired:
                model.kill()
                model.wait()
