"""分级告警引擎：视觉信号 → 预警工单，含去重、升级与处置建议。"""
from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Any

from ai.llm.analyzer import RiskAnalyzer
from ai.vision.service import analyze_window, describe

TYPE_ORDER = {"attack": 3, "fall": 2, "surround": 1}
TYPE_LABELS = {"attack": "疑似暴力攻击", "fall": "人员摔倒",
               "surround": "多人围堵", "bullying": "疑似校园霸凌",
               "danger_word": "危险言语告警", "seat": "图书馆占座",
               "behavior": "行为规范违规"}
BASE_SEVERITY = {"attack": "red", "fall": "orange", "surround": "yellow"}
SEVERITY_RANK = {"blue": 1, "yellow": 2, "orange": 3, "red": 4}


def new_alert_id() -> str:
    return f"ALM-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"


def new_event_id() -> str:
    return f"EVT-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"


class AlertEngine:
    def __init__(self, config: dict[str, Any],
                 analyzer: RiskAnalyzer | None = None) -> None:
        self.config = config
        self.vision_cfg = config.get("vision", {})
        self.alert_cfg = config.get("alert", {})
        self.analyzer = analyzer or RiskAnalyzer()
        self.dedupe_seconds = int(self.vision_cfg.get("dedupe_seconds", 60))
        self.escalate_signals = int(self.vision_cfg.get("escalate_signals", 2))
        self._recent: dict[tuple[str, str], float] = {}

    # ---------- 视觉告警 ----------
    def analyze_vision(self, frames: list, camera_id: str, location: str,
                       force: bool = False) -> list[dict[str, Any]]:
        signals = analyze_window(frames, self.vision_cfg)
        if not signals:
            return []

        kinds = {s.signal_type for s in signals}
        primary = max(kinds, key=lambda k: TYPE_ORDER.get(k, 0))
        alert_type = ("bullying" if "attack" in kinds and
                      ({"fall", "surround"} & kinds) else primary)

        severity = BASE_SEVERITY.get(primary, "yellow")
        # 多类信号同时出现 → 升级
        if len(kinds) >= self.escalate_signals:
            severity = "red"
        if alert_type == "bullying":
            severity = "red"

        now = time.time()
        key = (camera_id, alert_type)
        if not force and now - self._recent.get(key, 0.0) < self.dedupe_seconds:
            return []
        self._recent[key] = now

        confidence = max(s.confidence for s in signals)
        if len(kinds) >= 2:
            confidence = min(0.99, confidence + 0.05)

        evidence: dict[str, Any] = {
            "signals": [s.to_dict() for s in signals],
            "signal_text": describe(signals),
            "metrics": self._metrics(signals),
            "window_frames": len(frames) if isinstance(frames, list) else 0,
            "snapshot": self._snapshot(frames),
        }
        summary = (f"{location} 检测到{TYPE_LABELS.get(alert_type, alert_type)}"
                   f"（{evidence['signal_text']}）")
        evidence["advice"] = self.analyzer.recommendations(
            alert_type, severity, evidence)

        alert = {
            "alert_id": new_alert_id(),
            "event_id": new_event_id(),
            "alert_type": alert_type,
            "severity": severity,
            "confidence": round(confidence, 2),
            "camera_id": camera_id,
            "location": location,
            "summary": summary,
            "evidence": evidence,
            "status": "pending",
            "source": "vision",
            "occurred_at": now,
        }
        auto = self.alert_cfg.get("auto_deterrence_red", True)
        if auto and severity == "red":
            alert["auto_action"] = {
                "kind": "voice",
                "content": "警告！监测到紧急情况，安保人员正在赶来，请立即停止危险行为！",
                "status": "queued",
            }
        return [alert]

    def _metrics(self, signals: list) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for s in signals:
            out[s.signal_type] = s.detail
        return out

    def _snapshot(self, frames: list) -> dict[str, Any]:
        """保留末帧关键点，供前端绘制姿态骨架。"""
        if not isinstance(frames, list) or not frames:
            return {}
        last = frames[-1]
        return {"persons": last[:4] if isinstance(last, list) else []}

    # ---------- 语音/其他事件转预警 ----------
    def alert_from_event(self, event: dict[str, Any],
                         alert_type: str = "danger_word",
                         source: str = "intercom") -> dict[str, Any]:
        severity = event.get("severity", "yellow")
        if severity not in SEVERITY_RANK:
            severity = "yellow"
        detail = event.get("detail") or {}
        evidence = {
            "transcript": detail.get("transcript", ""),
            "matched": detail.get("matched", []),
            "signal_text": "语音危险词命中",
        }
        evidence["advice"] = self.analyzer.recommendations(
            alert_type, severity, evidence)
        return {
            "alert_id": new_alert_id(),
            "event_id": event.get("event_id", ""),
            "alert_type": alert_type,
            "severity": severity,
            "confidence": 0.88,
            "camera_id": event.get("source", ""),
            "location": event.get("location", ""),
            "summary": event.get("summary", ""),
            "evidence": evidence,
            "status": "pending",
            "source": source,
            "occurred_at": event.get("timestamp", time.time()),
        }

    @staticmethod
    def occurred_text(ts: float) -> str:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
