"""非结构化数据解析：语音/视频 → 结构化中间结果。

解析器通过 Provider 接口解耦：
- ASR：生产接 SenseVoice（funasr），离线/测试用旁挂文本（MockASR）。
- 视频：优先用 OpenCV 读取时长/帧率/分辨率，缺失时退化为文件元数据。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class MediaBundle:
    """一次事件的多模态素材包。"""
    event_id: str
    location: str
    audio_path: str | None = None
    video_path: str | None = None
    pose_data_path: str | None = None
    transcript: str | None = None      # 已有转写文本时跳过 ASR
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedMedia:
    transcript: str = ""
    pose_summary: list[dict[str, Any]] = field(default_factory=list)
    video_meta: dict[str, Any] = field(default_factory=dict)


class ASRProvider(Protocol):
    def transcribe(self, audio_path: str) -> str: ...


class VideoProvider(Protocol):
    def extract_meta(self, video_path: str) -> dict[str, Any]: ...


class SenseVoiceASR:
    """生产 ASR：本地 SenseVoice 推理；依赖缺失时构造报错，由上层回退。"""

    def __init__(self, model_dir: str = "iic/SenseVoiceSmall") -> None:
        try:
            from funasr import AutoModel  # type: ignore
        except ImportError as exc:
            raise RuntimeError("funasr 未安装，无法加载 SenseVoice") from exc
        self._model = AutoModel(model=model_dir)

    def transcribe(self, audio_path: str) -> str:
        result = self._model.generate(input=audio_path)
        return result[0]["text"] if result else ""


class MockASR:
    """离线/测试 ASR：约定 <音频名>.txt 旁挂文件即转写结果。"""

    def __init__(self, mapping: dict[str, str] | None = None) -> None:
        self.mapping = mapping or {}

    def transcribe(self, audio_path: str) -> str:
        if audio_path in self.mapping:
            return self.mapping[audio_path]
        sidecar = os.path.splitext(audio_path)[0] + ".txt"
        if os.path.exists(sidecar):
            with open(sidecar, encoding="utf-8") as f:
                return f.read().strip()
        return ""


class CvVideoProvider:
    """视频元数据提取：OpenCV 可用时读取真实时长/帧率，否则仅返回文件信息。"""

    def extract_meta(self, video_path: str) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "path": video_path,
            "size_bytes": os.path.getsize(video_path) if os.path.exists(video_path) else 0,
        }
        try:
            import cv2  # type: ignore

            cap = cv2.VideoCapture(video_path)
            if cap.isOpened():
                fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
                frames = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                meta.update({
                    "fps": round(fps, 2),
                    "frames": int(frames),
                    "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0),
                    "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0),
                    "duration_sec": round(frames / fps, 2) if fps > 0 else 0,
                })
                cap.release()
        except Exception:  # noqa: BLE001 - 元数据读取失败不影响报告生成
            pass
        sidecar = os.path.splitext(video_path)[0] + ".meta.json"
        if os.path.exists(sidecar):
            with open(sidecar, encoding="utf-8") as f:
                meta.update(json.load(f))
        return meta


def load_pose_summary(pose_data_path: str | None) -> list[dict[str, Any]]:
    if not pose_data_path or not os.path.exists(pose_data_path):
        return []
    with open(pose_data_path, encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else data.get("frames", [])


def parse_bundle(bundle: MediaBundle, asr: ASRProvider,
                 video: VideoProvider | None = None) -> ParsedMedia:
    """解析素材包：语音转写 + 姿态数据 + 视频元数据。"""
    parsed = ParsedMedia()
    if bundle.transcript:
        parsed.transcript = bundle.transcript
    elif bundle.audio_path:
        parsed.transcript = asr.transcribe(bundle.audio_path)
    parsed.pose_summary = load_pose_summary(bundle.pose_data_path)
    if bundle.video_path:
        parsed.video_meta = (video or CvVideoProvider()).extract_meta(bundle.video_path)
    return parsed
