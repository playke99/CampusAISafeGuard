"""行为规范违规处理：检测 → 人脸匹配 → 违规事件 → Agent 推送归档。

人脸未匹配时事件照常记录并标记 needs_review，供纪律部门人工复核。
"""
from __future__ import annotations

from typing import Any, Protocol

from campus_ai.common.bus import MessageBus, TOPIC_ALERT
from campus_ai.common.events import EventType, Severity, make_event

from .face_match import FaceMatcher
from .inference import BehaviorDetector, Detection

LABEL_CN = {"slippers": "穿拖鞋进入教学区域",
            "breakfast": "携带早餐进入教学区域"}


class AgentDispatcher(Protocol):
    def dispatch_event(self, event) -> dict[str, Any]: ...


class BehaviorViolationService:
    def __init__(self, detector: BehaviorDetector, face_matcher: FaceMatcher,
                 agent: AgentDispatcher, bus: MessageBus,
                 camera_id: str, location: str, targets: list[str]) -> None:
        self.detector = detector
        self.face_matcher = face_matcher
        self.agent = agent
        self.bus = bus
        self.camera_id = camera_id
        self.location = location
        self.targets = targets

    def process_frame(self, frame=None) -> list[dict[str, Any]]:
        return self.process_detections(self.detector.detect(frame))

    def process_detections(self, detections: list[Detection]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for det in detections:
            student = self.face_matcher.match(det.face_ref or "")
            identified = bool(student.get("student_id"))
            who = (f"{student['major']}{student['class_name']} {student['name']}"
                   if identified else "身份待确认")
            event = make_event(
                EventType.VIOLATION, Severity.BLUE,
                source=self.camera_id, location=self.location,
                summary=f"违规记录：{who} {LABEL_CN.get(det.label, det.label)}",
                detail={
                    "violation": det.label,
                    "violation_cn": LABEL_CN.get(det.label, det.label),
                    "confidence": round(det.conf, 3),
                    "student": student,
                    "identified": identified,
                    "needs_review": not identified,
                },
                targets=list(self.targets),
            )
            self.bus.publish(TOPIC_ALERT, event)
            push = self.agent.dispatch_event(event)
            results.append({
                "event": event.to_dict(),
                "event_id": event.event_id,
                "violation": det.label,
                "identified": identified,
                "student": student if identified else None,
                "needs_review": not identified,
                "push_ok": push.get("push_ok", False),
                "targets": push.get("targets", []),
            })
        return results
