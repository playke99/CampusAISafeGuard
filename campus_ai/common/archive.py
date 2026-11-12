"""JSONL 归档：报告、告警、工单、违规记录统一落盘，供查询与演示核对。"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


class JsonlArchive:
    """线程安全的追加式归档，读取返回最新在前。"""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, record: dict[str, Any]) -> None:
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self._lock:
            with open(self.path, encoding="utf-8") as f:
                records = [json.loads(line) for line in f if line.strip()]
        return list(reversed(records))[:max(0, limit)]

    def count(self) -> int:
        if not self.path.exists():
            return 0
        with self._lock:
            with open(self.path, encoding="utf-8") as f:
                return sum(1 for line in f if line.strip())
