"""统一事件模型：告警、工单、违规、报告、制止记录共用一份事件契约。

所有事件带全局唯一 event_id，贯穿解析、报告、工单、推送与归档，便于追溯。
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class Severity(str, Enum):
    """预警级别：红 > 橙 > 黄 > 蓝。"""

    RED = "red"
    ORANGE = "orange"
    YELLOW = "yellow"
    BLUE = "blue"

    @property
    def rank(self) -> int:
        return {"blue": 1, "yellow": 2, "orange": 3, "red": 4}[self.value]


class EventType(str, Enum):
    ALERT = "alert"                    # 实时告警（危险词）
    WORK_ORDER = "work_order"          # 待处理工单（霸座）
    VIOLATION = "violation"            # 行为规范违规
    STRUCTURED_REPORT = "report"       # Agent 结构化报告
    DETERRENCE = "deterrence"          # 口头制止播放记录


def _new_id(prefix: str) -> str:
    return f"{prefix}-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"


@dataclass
class BaseEvent:
    event_type: EventType
    severity: Severity
    source: str
    location: str
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: _new_id("EVT"))
    timestamp: float = field(default_factory=time.time)
    targets: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["event_type"] = self.event_type.value
        data["severity"] = self.severity.value
        return data


def make_event(event_type: EventType, severity: Severity, source: str,
               location: str, summary: str, **kw: Any) -> BaseEvent:
    return BaseEvent(event_type=event_type, severity=severity, source=source,
                     location=location, summary=summary, **kw)
