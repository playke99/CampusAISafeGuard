"""服务启动入口。

    python -m server.main
    python -m server.main --port 8100 --config config/app.json
"""
from __future__ import annotations

import argparse
import logging

import uvicorn

from server.api import create_app
from server.core.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="数智卫校校园安全 AI 预警与干预系统")
    parser.add_argument("--config", default=None, help="config/app.json 路径")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--host", default=None)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    cfg = load_config(args.config)
    host = args.host or cfg["app"]["host"]
    port = args.port or int(cfg["app"]["port"])

    app = create_app(cfg)
    logging.getLogger("campus").info(
        "%s v%s 启动：http://127.0.0.1:%d", cfg["app"]["name"],
        cfg["app"]["version"], port)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
