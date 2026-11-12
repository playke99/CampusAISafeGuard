"""对讲设备服务：收音 → 转写 → 危险词匹配 → 告警推送 + 口头制止。

命中即触发告警推送与本地制止话术播放；未命中返回 None，不产生事件。
"""
from __future__ import annotations

import time
from typing import Any

from campus_ai.common.bus import MessageBus, TOPIC_ALERT
from campus_ai.common.events import EventType, Severity, make_event
from campus_ai.common.push_gateway import PushGateway

from campus_ai.agent_service.parsers import ASRProvider

from .audio_io import AudioSink, AudioSource
from .danger_words import DangerWordMatcher

ALERT_TARGETS = {
    Severity.RED: ["security_leader", "duty_principal"],
    Severity.ORANGE: ["security_staff", "head_teacher"],
    Severity.YELLOW: ["security_staff"],
}


class IntercomService:
    def __init__(self, source: AudioSource, sink: AudioSink, asr: ASRProvider,
                 matcher: DangerWordMatcher, gateway: PushGateway, bus: MessageBus,
                 device_id: str, location: str,
                 deterrence_phrases: dict[str, str]) -> None:
        self.source = source
        self.sink = sink
        self.asr = asr
        self.matcher = matcher
        self.gateway = gateway
        self.bus = bus
        self.device_id = device_id
        self.location = location
        self.deterrence_phrases = deterrence_phrases

    def deterrence_for(self, severity: Severity) -> str:
        return self.deterrence_phrases.get(severity.value, "")

    def handle_utterance(self, audio_path: str | None = None,
                         transcript: str | None = None) -> dict[str, Any] | None:
        """处理一段收音：转写 → 匹配；命中则告警 + 口头制止。"""
        text = transcript if transcript is not None else (
            self.asr.transcribe(audio_path) if audio_path else "")
        result = self.matcher.match(text)
        if not result.hit:
            return None

        severity = result.severity
        event = make_event(
            EventType.ALERT, severity,
            source=self.device_id, location=self.location,
            summary=f"危险词告警（{severity.value}）：命中 {','.join(result.matched)}",
            detail={"transcript": text, "matched": result.matched},
            targets=ALERT_TARGETS.get(severity, ["security_staff"]),
        )
        self.bus.publish(TOPIC_ALERT, event)
        push_result = self.gateway.push(event, event.targets)

        phrase = self.deterrence_for(severity)
        if phrase:
            self.sink.play(phrase)
            deterrence = make_event(
                EventType.DETERRENCE, severity,
                source=self.device_id, location=self.location,
                summary=f"已播放制止话术（{severity.value}）",
                detail={"phrase": phrase, "trigger_event": event.event_id},
            )
            self.bus.publish(TOPIC_ALERT, deterrence)

        return {
            "event": event.to_dict(),
            "event_id": event.event_id,
            "transcript": text,
            "matched": result.matched,
            "severity": severity.value,
            "push_ok": push_result.get("ok", False),
            "deterrence_played": bool(phrase),
            "deterrence_phrase": phrase,
        }

    def run(self, max_rounds: int = 100, idle_sleep: float = 0.0) -> int:
        """采集主循环；返回触发告警次数。"""
        alerts = 0
        for _ in range(max_rounds):
            audio = self.source.capture()
            if audio is None:
                if getattr(self.source, "exhausted", lambda: False)():
                    break
                if idle_sleep:
                    time.sleep(idle_sleep)
                continue
            if self.handle_utterance(audio_path=audio):
                alerts += 1
        return alerts
