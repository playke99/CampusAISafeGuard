"""计划2 单元测试：危险词匹配、对讲告警与口头制止。"""
import json

from campus_ai.common.bus import InMemoryBus, TOPIC_ALERT
from campus_ai.common.events import Severity
from campus_ai.common.push_gateway import RecordingPushGateway
from campus_ai.intercom_service.audio_io import (RecordingSpeakerSink,
                                                 ScriptedMicSource)
from campus_ai.intercom_service.danger_words import DangerWordMatcher, normalize
from campus_ai.intercom_service.service import IntercomService

PHRASES = {"red": "请立即停止危险行为！", "orange": "请立即停止，保持冷静！",
           "yellow": "请文明用语。"}


def _service():
    gateway = RecordingPushGateway()
    sink = RecordingSpeakerSink()
    bus = InMemoryBus()
    service = IntercomService(
        ScriptedMicSource([]), sink, asr=None,  # type: ignore[arg-type]
        matcher=DangerWordMatcher(), gateway=gateway, bus=bus,
        device_id="intercom-01", location="图书馆3层",
        deterrence_phrases=PHRASES)
    return service, gateway, sink, bus


def test_normalize_removes_punctuation_and_space():
    assert normalize(" 救 命，！") == "救命"


def test_matcher_levels_and_dedup(tmp_path):
    matcher = DangerWordMatcher({"red": ["救命", " 救命 ", ""],
                                 "orange": ["别打我"], "yellow": ["滚"]})
    assert matcher.match("快救命啊").severity == Severity.RED
    assert matcher.match("你别打我").severity == Severity.ORANGE
    assert matcher.match("滚出去").severity == Severity.YELLOW
    assert matcher.match("今天天气不错").hit is False

    path = tmp_path / "words.json"
    path.write_text(json.dumps({"red": ["报警"]}, ensure_ascii=False), encoding="utf-8")
    assert DangerWordMatcher.from_file(str(path)).match("我要报警").hit is True


def test_intercom_hit_triggers_alert_and_deterrence():
    service, gateway, sink, bus = _service()
    result = service.handle_utterance(transcript="你别打我，我错了")
    assert result and result["severity"] == "orange"
    assert result["matched"] == ["别打我", "我错了"]
    assert result["deterrence_played"] is True
    assert sink.played == [PHRASES["orange"]]
    assert gateway.pushed_to("security_staff")
    assert len(bus.events_of(TOPIC_ALERT)) == 2  # 告警 + 制止记录


def test_intercom_no_hit_returns_none():
    service, gateway, sink, _ = _service()
    assert service.handle_utterance(transcript="今天图书馆人很多") is None
    assert gateway.records == [] and sink.played == []


def test_intercom_run_scripted_source():
    service, gateway, sink, _ = _service()
    service.source = ScriptedMicSource([None, None])
    assert service.run(max_rounds=5) == 0
    service.source = ScriptedMicSource([])
    assert service.run(max_rounds=5) == 0
