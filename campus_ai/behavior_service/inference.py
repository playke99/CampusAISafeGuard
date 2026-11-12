"""违规行为推理：穿拖鞋 / 携带早餐进入教学区域。

- YoloBehaviorDetector：加载训练权重 best.pt 做真实推理。
- MockBehaviorDetector：脚本化检测结果，供演示与测试。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class Detection:
    label: str                                # slippers / breakfast
    conf: float
    bbox: tuple[float, float, float, float]   # xyxy
    face_ref: str | None = None               # 关联的人脸截图/特征引用


class BehaviorDetector(Protocol):
    def detect(self, frame: Any) -> list[Detection]: ...


class YoloBehaviorDetector:
    def __init__(self, model_path: str, conf: float = 0.5) -> None:
        try:
            from ultralytics import YOLO  # type: ignore
        except ImportError as exc:
            raise RuntimeError("ultralytics 未安装，无法使用 YoloBehaviorDetector") from exc
        self._model = YOLO(model_path)
        self.conf = conf

    def detect(self, frame: Any) -> list[Detection]:
        out: list[Detection] = []
        for box in self._model(frame, verbose=False)[0].boxes:
            confidence = float(box.conf)
            if confidence < self.conf:
                continue
            out.append(Detection(
                label=self._model.names[int(box.cls)],
                conf=confidence,
                bbox=tuple(float(x) for x in box.xyxy[0].tolist()),
            ))
        return out


class MockBehaviorDetector:
    """脚本化检测：每帧返回预设检测列表，耗尽后返回空。"""

    def __init__(self, frames: list[list[Detection]] | None = None) -> None:
        self._frames = list(frames or [])
        self._idx = 0

    def detect(self, frame: Any = None) -> list[Detection]:
        if self._idx < len(self._frames):
            out = self._frames[self._idx]
            self._idx += 1
            return list(out)
        return []
