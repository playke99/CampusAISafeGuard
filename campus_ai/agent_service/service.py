"""Agent 智能体服务：解析 → 深判 → 报告 → 定向推送 → 上总线。"""
from __future__ import annotations

from typing import Any

from campus_ai.common.bus import MessageBus, TOPIC_REPORT
from campus_ai.common.events import BaseEvent, EventType, Severity, make_event
from campus_ai.common.push_gateway import PushGateway

from .llm_client import LLMClient
from .parsers import ASRProvider, MediaBundle, VideoProvider, parse_bundle
from .report_builder import render_report


class AgentService:
    def __init__(self, llm: LLMClient, asr: ASRProvider, gateway: PushGateway,
                 bus: MessageBus, route: dict[str, list[str]],
                 video: VideoProvider | None = None) -> None:
        self.llm = llm
        self.asr = asr
        self.video = video
        self.gateway = gateway
        self.bus = bus
        self.route = route

    def targets_for(self, severity: Severity) -> list[str]:
        return list(self.route.get(severity.value, []))

    def dispatch_event(self, event: BaseEvent) -> dict[str, Any]:
        """外部模块事件入口：违规、工单等统一走 Agent 推送与留档。"""
        targets = event.targets or self.targets_for(event.severity)
        result = self.gateway.push(event, targets)
        self.bus.publish(TOPIC_REPORT, event)
        return {"event_id": event.event_id, "targets": targets,
                "push_ok": result.get("ok", False)}

    def process(self, bundle: MediaBundle) -> dict[str, Any]:
        """全流程：解析 → 深判 → 报告 → 定向推送。"""
        parsed = parse_bundle(bundle, self.asr, self.video)
        report = self.llm.analyze(bundle, parsed)
        severity = Severity(report["severity"])
        text = render_report(report, bundle.event_id, bundle.location)

        event = make_event(
            EventType.STRUCTURED_REPORT, severity,
            source="agent", location=bundle.location,
            summary=report["summary"],
            detail={"report": report, "report_text": text,
                    "transcript": parsed.transcript,
                    "video_meta": parsed.video_meta},
        )
        targets = self.targets_for(severity)
        push_result = self.gateway.push(event, targets)
        self.bus.publish(TOPIC_REPORT, event)
        return {
            "event": event.to_dict(),
            "event_id": event.event_id,
            "severity": severity.value,
            "targets": targets,
            "push_ok": push_result.get("ok", False),
            "report": report,
            "report_text": text,
            "transcript": parsed.transcript,
            "video_meta": parsed.video_meta,
        }
