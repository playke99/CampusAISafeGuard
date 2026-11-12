"""计划1 单元测试：音视频解析 → 结构化报告 → 定向推送。"""
import json

from campus_ai.agent_service.llm_client import (MockLLMClient, extract_json,
                                                validate_report)
from campus_ai.agent_service.parsers import MediaBundle, MockASR, parse_bundle
from campus_ai.agent_service.report_builder import render_report
from campus_ai.agent_service.service import AgentService
from campus_ai.common.bus import InMemoryBus
from campus_ai.common.events import EventType, Severity, make_event
from campus_ai.common.push_gateway import RecordingPushGateway

ROUTE = {"red": ["security_leader"], "orange": ["security_staff"],
         "yellow": ["security_staff"], "blue": ["security_staff"]}


def _service():
    gateway = RecordingPushGateway()
    bus = InMemoryBus()
    service = AgentService(MockLLMClient(), MockASR(), gateway, bus, ROUTE)
    return service, gateway, bus


def test_parse_bundle_uses_sidecar_transcript(tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")
    (tmp_path / "a.txt").write_text("有人在楼道呼救", encoding="utf-8")
    parsed = parse_bundle(MediaBundle("EVT-1", "教学楼A栋", audio_path=str(audio)), MockASR())
    assert parsed.transcript == "有人在楼道呼救"


def test_parse_bundle_accepts_direct_transcript():
    parsed = parse_bundle(MediaBundle("EVT-1", "图书馆", transcript="直接文本"), MockASR())
    assert parsed.transcript == "直接文本"


def test_llm_validation_falls_back():
    report = validate_report({"severity": "非法", "involved_count": "abc"})
    assert report["severity"] == "blue"
    assert report["involved_count"] == 0
    assert report["timeline"] == []


def test_extract_json_from_noisy_output():
    assert extract_json('说明文字\n{"a": 1}\n结束') == {"a": 1}


def test_report_render_contains_sections():
    text = render_report({"event_type": "test", "severity": "red",
                          "summary": "摘要", "timeline": ["t1"],
                          "involved_count": 2, "suggestion": "处置"}, "EVT-1", "地点")
    assert "# 校园安全事件分析报告" in text
    assert "报告编号：EVT-1" in text
    assert "红色（极高）" in text


def test_agent_process_red_and_push_targets():
    service, gateway, bus = _service()
    bundle = MediaBundle("EVT-RED", "图书馆3层", transcript="救命，有人打我")
    result = service.process(bundle)
    assert result["severity"] == "red"
    assert result["targets"] == ["security_leader"]
    assert result["push_ok"] is True
    assert "报告编号" in result["report_text"]
    assert gateway.records[0]["targets"] == ["security_leader"]
    assert bus.published  # 报告已上总线


def test_agent_dispatch_event_to_agent_targets():
    service, gateway, _ = _service()
    event = make_event(EventType.VIOLATION, Severity.BLUE, "cam", "教学楼",
                       "违规记录", targets=["discipline_office"])
    result = service.dispatch_event(event)
    assert result["targets"] == ["discipline_office"]
    assert gateway.records[0]["event_type"] == "violation"
    json.dumps(result, ensure_ascii=False)
