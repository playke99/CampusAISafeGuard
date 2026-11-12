"""VAD 端点检测：基于短时能量的轻量语音活动检测（零三方依赖）。

将 16bit/16kHz 单声道 PCM 切分为 25ms 帧，平均幅度连续超门限视为语音起点，
段内连续静音超过 300ms 视为端点。生产环境可替换为 Silero-VAD。
"""
from __future__ import annotations

import array
from dataclasses import dataclass, field


def _avg_amplitude(frame: bytes) -> float:
    """16bit 小端 PCM 平均绝对幅度。"""
    samples = array.array("h")
    samples.frombytes(frame)
    if not samples:
        return 0.0
    return sum(abs(s) for s in samples) / len(samples)


@dataclass
class SpeechSegment:
    start_sec: float
    end_sec: float
    pcm: bytes = field(repr=False, default=b"")

    @property
    def duration(self) -> float:
        return round(self.end_sec - self.start_sec, 3)


class EnergyVAD:
    def __init__(self, sample_rate: int = 16000,
                 energy_threshold: float = 500.0,
                 frame_ms: int = 25,
                 max_silence_ms: int = 300) -> None:
        self.sample_rate = sample_rate
        self.energy_threshold = energy_threshold
        # 每帧字节数 = 采样率 × 帧长(秒) × 2 字节
        self.frame_bytes = int(sample_rate * frame_ms / 1000) * 2
        self.max_silence_frames = max(1, max_silence_ms // frame_ms)

    def detect(self, pcm: bytes) -> list[SpeechSegment]:
        segments: list[SpeechSegment] = []
        in_speech = False
        silence_run = 0
        seg_start = 0
        speech_buf = bytearray()

        for offset in range(0, len(pcm) - self.frame_bytes + 1, self.frame_bytes):
            frame = pcm[offset:offset + self.frame_bytes]
            energy = _avg_amplitude(frame)
            if energy >= self.energy_threshold:
                if not in_speech:
                    in_speech = True
                    seg_start = offset
                silence_run = 0
                speech_buf += frame
            elif in_speech:
                silence_run += 1
                speech_buf += frame
                if silence_run >= self.max_silence_frames:
                    end = offset + self.frame_bytes - silence_run * self.frame_bytes
                    seg_pcm = bytes(speech_buf[:end - seg_start])
                    if len(seg_pcm) >= self.frame_bytes:
                        segments.append(SpeechSegment(
                            start_sec=seg_start / 2 / self.sample_rate,
                            end_sec=end / 2 / self.sample_rate,
                            pcm=seg_pcm))
                    in_speech = False
                    silence_run = 0
                    speech_buf = bytearray()

        if in_speech and len(speech_buf) >= self.frame_bytes:
            segments.append(SpeechSegment(
                start_sec=seg_start / 2 / self.sample_rate,
                end_sec=(seg_start + len(speech_buf)) / 2 / self.sample_rate,
                pcm=bytes(speech_buf)))
        return segments
