"""推送网关：把事件定向推送到管理端。

- HttpPushGateway：POST 到后端推送接口，带超时与指数退避重试（生产）。
- RecordingPushGateway：内存记录全部推送，供测试/场景模拟断言。
- JsonlPushGateway：无后端时写入本地推送队列，保证"不丢消息"（演示兜底）。
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from typing import Any, Protocol

from .events import BaseEvent


class PushGateway(Protocol):
    def push(self, event: BaseEvent, targets: list[str]) -> dict[str, Any]: ...


class HttpPushGateway:
    def __init__(self, endpoint: str, token: str = "", timeout: float = 3.0,
                 max_retry: int = 3) -> None:
        self.endpoint = endpoint
        self.token = token
        self.timeout = timeout
        self.max_retry = max_retry

    def push(self, event: BaseEvent, targets: list[str]) -> dict[str, Any]:
        payload = json.dumps({"event": event.to_dict(), "targets": targets},
                             ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        last_error: Exception | None = None
        for attempt in range(self.max_retry):
            req = urllib.request.Request(self.endpoint, data=payload, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return {"ok": 200 <= resp.status < 300, "status": resp.status,
                            "attempts": attempt + 1}
            except Exception as exc:  # noqa: BLE001 - 网络异常统一重试
                last_error = exc
                time.sleep(min(2 ** attempt, 4) * 0.1)
        return {"ok": False, "error": str(last_error), "attempts": self.max_retry}


class RecordingPushGateway:
    def __init__(self, fail_times: int = 0) -> None:
        self.records: list[dict[str, Any]] = []
        self._fail_times = fail_times

    def push(self, event: BaseEvent, targets: list[str]) -> dict[str, Any]:
        if self._fail_times > 0:
            self._fail_times -= 1
            return {"ok": False, "error": "simulated failure"}
        self.records.append({
            "event_id": event.event_id,
            "event_type": event.event_type.value,
            "severity": event.severity.value,
            "targets": list(targets),
            "summary": event.summary,
        })
        return {"ok": True, "status": 200, "attempts": 1}

    def pushed_to(self, target: str) -> list[dict[str, Any]]:
        return [r for r in self.records if target in r["targets"]]


class JsonlPushGateway:
    """本地推送队列：写入 JSONL，后端恢复后可按序补推。"""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def push(self, event: BaseEvent, targets: list[str]) -> dict[str, Any]:
        record = {
            "pushed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "targets": list(targets),
            "event": event.to_dict(),
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return {"ok": True, "status": "queued", "attempts": 1}
