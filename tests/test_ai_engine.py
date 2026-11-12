"""AI 感知与告警引擎单元测试。"""
from __future__ import annotations

import struct

from ai.alert_engine import AlertEngine
from ai.audio.danger_words import DangerWordMatcher
from ai.llm.analyzer import RiskAnalyzer
from ai.vision.events import detect_attack, detect_fall, detect_surround
from demo import pose_builder
from server.core.config import load_config


def _cfg():
    cfg = load_config()
    cfg["vision"]["dedupe_seconds"] = 60
    return cfg


def test_pose_builder_events():
    assert detect_fall(pose_builder.standing(), 65) is None
    assert detect_fall(pose_builder.fallen(), 65)["tilt_angle"] > 65

    attack_frames = [f[0] for f in pose_builder.attack_window()]
    assert detect_attack(attack_frames, 3.0)["z_score"] > 3.0

    surround = pose_builder.surround_frame()[0]
    assert detect_surround(surround, 3.5) is not None


def test_alert_engine_types_and_escalation():
    engine = AlertEngine(_cfg())
    cases = {
        "attack": ("attack", "red"),
        "fall": ("fall", "orange"),
        "surround": ("surround", "yellow"),
        "bullying": ("bullying", "red"),
    }
    for name, (kind, severity) in cases.items():
        frames, camera, location, _ = pose_builder.build(name)
        alerts = engine.analyze_vision(frames, camera, location, force=True)
        assert len(alerts) == 1, name
        assert alerts[0]["alert_type"] == kind
        assert alerts[0]["severity"] == severity
        assert alerts[0]["evidence"]["advice"]


def test_alert_dedupe_and_force():
    engine = AlertEngine(_cfg())
    frames, camera, location, _ = pose_builder.build("attack")
    assert engine.analyze_vision(frames, "dedupe-cam", location)
    assert engine.analyze_vision(frames, "dedupe-cam", location) == []
    assert engine.analyze_vision(frames, "dedupe-cam", location, force=True)


def test_danger_words_levels():
    matcher = DangerWordMatcher.from_file("config/danger_words.json")
    assert matcher.match("救命！有人打我").severity == "red"
    assert matcher.match("别打我，放过我").severity == "orange"
    assert matcher.match("今天天气不错").hit is False


def test_analyzer_advice_and_report():
    analyzer = RiskAnalyzer()
    advice = analyzer.recommendations("attack", "red", {})
    assert any("安保" in item for item in advice)
    report = analyzer.deep_report({
        "alert_id": "ALM-TEST", "event_id": "EVT-TEST",
        "alert_type": "bullying", "severity": "red",
        "location": "宿舍楼", "camera_id": "cam-1", "confidence": 0.96,
        "summary": "检测到霸凌冲突",
        "evidence": {"signal_text": "攻击 + 围堵", "advice": advice},
    })
    assert "处置建议" in report and "ALM-TEST" in report


def test_energy_vad_segments():
    from ai.audio.vad import EnergyVAD

    def pcm(amplitude: int, seconds: float, rate: int = 16000) -> bytes:
        count = int(rate * seconds)
        return struct.pack("<%dh" % count, *([amplitude] * count))

    stream = pcm(0, 0.3) + pcm(3000, 0.4) + pcm(0, 0.6)
    segments = EnergyVAD().detect(stream)
    assert len(segments) == 1
    assert 0.2 < segments[0].duration < 0.7
