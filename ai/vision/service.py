"""视觉感知服务：对一段关键点窗口做多事件分析，输出结构化信号。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from . import events


@dataclass
class VisionSignal:
    signal_type: str                 # fall/attack/surround
    person: int
    confidence: float
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _validate_frames(frames: Any) -> list[list[list[list[float]]]]:
    """标准化为 [frame][person][17][2]，过滤非法帧。"""
    if not isinstance(frames, list):
        return []
    out: list[list[list[list[float]]]] = []
    for frame in frames:
        if not isinstance(frame, list):
            continue
        persons: list[list[list[float]]] = []
        for person in frame:
            if not events.valid_person(person):
                continue
            persons.append([[float(p[0]), float(p[1])] for p in person[:17]])
        out.append(persons)
    return out


def analyze_window(frames: list, cfg: dict[str, Any]) -> list[VisionSignal]:
    """窗口分析：摔倒取末帧，攻击看时序，围堵看末帧多人几何关系。"""
    window = _validate_frames(frames)
    if not window:
        return []
    last = window[-1]
    signals: list[VisionSignal] = []

    fall_threshold = float(cfg.get("fall_angle", 65.0))
    for idx, person in enumerate(last):
        hit = events.detect_fall(person, fall_threshold)
        if hit:
            # 倾斜越接近 90°，置信度越高
            angle = hit["tilt_angle"]
            conf = min(0.98, 0.72 + max(0.0, angle - fall_threshold) / 120)
            signals.append(VisionSignal("fall", idx, round(conf, 2), hit))

    if len(window) >= int(cfg.get("min_frames", 3)):
        z_threshold = float(cfg.get("attack_z", 3.0))
        person_count = min(len(f) for f in window)
        for idx in range(person_count):
            series = [frame[idx] for frame in window]
            hit = events.detect_attack(series, z_threshold)
            if hit:
                z = hit["z_score"]
                conf = min(0.98, 0.65 + (z - z_threshold) * 0.06)
                signals.append(VisionSignal("attack", idx, round(conf, 2), hit))

    ratio = float(cfg.get("surround_ratio", 3.5))
    hit = events.detect_surround(last, ratio)
    if hit:
        # 人数越多、间距越近，置信度越高
        closeness = 1 - hit["distance"] / max(hit["reference"], 1e-6)
        conf = min(0.96, 0.66 + closeness * 0.2 + min(len(last), 5) * 0.02)
        hit["person_count"] = len(last)
        signals.append(VisionSignal("surround", hit["person_pair"][1],
                                    round(conf, 2), hit))

    return signals


def describe(signals: list[VisionSignal]) -> str:
    """生成可读的信号摘要，用于证据留档。"""
    names = {"fall": "人员摔倒", "attack": "疑似攻击", "surround": "多人围堵"}
    return "、".join(f"{names.get(s.signal_type, s.signal_type)}(P{s.person})"
                    for s in signals) or "无异常"
