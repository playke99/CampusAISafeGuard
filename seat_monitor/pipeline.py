"""流水线编排 —— 采集 / 推理 / 状态引擎 / 事件推送 四个独立线程

装配关系
--------
CaptureWorker --LatestFrameSlot--> InferenceWorker --DetectionQueue--> StateEngineWorker
                                                                          |
                                                                          v
                                                                   EventDispatcher --> Agent(8100)

每个模块都是独立对象，可单独 start/stop 与单独 health()；
任一模块停止或异常，其余模块继续运行（通过队列/邮箱解耦，无共享锁）。
"""
import logging
import time
from typing import Any, Callable, Dict, Optional

from .capture import CaptureWorker, LatestFrameSlot
from .inference import DetectionQueue, InferenceWorker
from .state_engine import EventDispatcher, StateEngineWorker

logger = logging.getLogger(__name__)

COMPONENTS = ("capture", "inference", "state_engine", "event_dispatcher")


class MonitorPipeline:
    def __init__(self, capture: CaptureWorker, inference: InferenceWorker,
                 engine: StateEngineWorker, dispatcher: EventDispatcher,
                 detector, slot: Optional[LatestFrameSlot] = None):
        self.capture = capture
        self.inference = inference
        self.engine = engine
        self.dispatcher = dispatcher
        self.detector = detector
        self.slot = slot or capture.slot
        self._components = {
            "capture": capture,
            "inference": inference,
            "state_engine": engine,
            "event_dispatcher": dispatcher,
        }

    # ── 整体启停 ─────────────────────────────────────────
    def start_all(self) -> dict:
        started = {}
        for name in COMPONENTS:
            started[name] = self._components[name].start()
        return started

    def stop_all(self) -> None:
        for name in reversed(COMPONENTS):
            try:
                self._components[name].stop()
            except Exception as e:
                logger.warning("停止 %s 失败: %s", name, e)

    # ── 单模块启停（独立维护/独立演练） ────────────────────
    def start(self, name: str) -> bool:
        if name not in self._components:
            raise KeyError(f"未知模块: {name}，可选 {COMPONENTS}")
        return self._components[name].start()

    def stop(self, name: str) -> None:
        if name not in self._components:
            raise KeyError(f"未知模块: {name}，可选 {COMPONENTS}")
        self._components[name].stop()

    def is_running(self, name: str) -> bool:
        return self._components[name].is_alive()

    # ── 健康检查 ─────────────────────────────────────────
    def health(self) -> dict:
        components = {name: comp.health() for name, comp in self._components.items()}
        detector_status = getattr(self.detector, "status", {}) or {}
        return {
            "running": {name: comp.is_alive() for name, comp in self._components.items()},
            "components": components,
            "queues": {
                "detections": self.inference.out_queue.stats(),
                "frame_slot": self.slot.stats(),
                "push": {
                    "size": self.dispatcher.health()["queue_size"],
                    "dropped": self.dispatcher.health()["dropped"],
                },
            },
            "detector": detector_status,
            "degraded": bool(detector_status.get("degraded")),
            "degraded_message": detector_status.get("message", ""),
            "server_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }


def build_pipeline(settings, machine, detector,
                   slot: Optional[LatestFrameSlot] = None,
                   roi_provider: Optional[Callable[[], list]] = None,
                   agent_url: Optional[str] = None,
                   push_sink: Optional[Callable[[dict], bool]] = None,
                   capture_opener: Optional[Callable[[str], Any]] = None,
                   preprocessor=None, background=None,
                   queue_size: int = 2) -> MonitorPipeline:
    """按配置装配四个模块；外部可注入 opener / push_sink 便于测试"""
    slot = slot or LatestFrameSlot()
    detections = DetectionQueue(maxsize=queue_size)
    source = str(settings.get("source"))
    interval = float(settings.get("detect_interval_sec") or 1.0)

    capture = CaptureWorker(source, slot=slot, opener=capture_opener)
    inference = InferenceWorker(slot, detections, detector,
                                interval_sec=interval,
                                roi_provider=roi_provider,
                                preprocessor=preprocessor,
                                background=background)

    if push_sink is None:
        url = agent_url or str(settings.get("agent_url") or "")
        token = str(settings.get("agent_token") or "")

        def push_sink(event: dict) -> bool:   # noqa: F811
            from .agent_client import push_seat_event
            return push_seat_event(event, url, token)

    dispatcher = EventDispatcher(push_sink)
    engine = StateEngineWorker(machine, detections, event_sink=dispatcher.enqueue)
    return MonitorPipeline(capture, inference, engine, dispatcher,
                           detector=detector, slot=slot)
