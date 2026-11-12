"""对讲设备音频 IO 抽象：收音（采集）与播放（制止话术）。

- 生产：MicSource（sounddevice 采集）、SpeakerSink（pyttsx3 播放）。
- 测试/演示：ScriptedMicSource 按脚本产出音频路径，RecordingSpeakerSink 记录播放内容。
"""
from __future__ import annotations

from typing import Protocol


class AudioSource(Protocol):
    """产出一段待识别音频（wav 路径）；无语音活动返回 None。"""

    def capture(self) -> str | None: ...


class AudioSink(Protocol):
    """播放一段提示音/话术文本。"""

    def play(self, text: str) -> None: ...


class MicSource:
    """生产收音：sounddevice 定长采集写 wav。"""

    def __init__(self, seconds: float = 3.0, sample_rate: int = 16000,
                 out_dir: str = "capture") -> None:
        try:
            import sounddevice  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("sounddevice 未安装，无法使用 MicSource") from exc
        self.seconds = seconds
        self.sample_rate = sample_rate
        self.out_dir = out_dir

    def capture(self) -> str | None:
        import os
        import time
        import wave

        import sounddevice as sd

        os.makedirs(self.out_dir, exist_ok=True)
        audio = sd.rec(int(self.seconds * self.sample_rate),
                       samplerate=self.sample_rate, channels=1, dtype="int16")
        sd.wait()
        path = os.path.join(self.out_dir, f"cap_{int(time.time() * 1000)}.wav")
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(audio.tobytes())
        return path


class SpeakerSink:
    """生产播放：TTS 合成后播放。"""

    def __init__(self) -> None:
        try:
            import pyttsx3  # type: ignore
        except ImportError as exc:
            raise RuntimeError("pyttsx3 未安装，无法使用 SpeakerSink") from exc
        self._engine = pyttsx3.init()

    def play(self, text: str) -> None:
        self._engine.say(text)
        self._engine.runAndWait()


class ScriptedMicSource:
    """演示/测试收音：按脚本依次返回音频片段路径（None 表示静音帧）。"""

    def __init__(self, script: list[str | None]) -> None:
        self._script = list(script)

    def capture(self) -> str | None:
        return self._script.pop(0) if self._script else None

    def exhausted(self) -> bool:
        return not self._script


class RecordingSpeakerSink:
    """演示/测试播放：记录播放内容。"""

    def __init__(self) -> None:
        self.played: list[str] = []

    def play(self, text: str) -> None:
        self.played.append(text)
