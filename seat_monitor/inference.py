"""推理线程 —— 只负责"取最新帧 -> 推理 -> 产出检测包"

通信接口
--------
LatestFrameSlot --读取--> 推理线程 --写入--> DetectionQueue --读取--> 状态引擎
- 输入邮箱容量 1：永远只处理最新帧，积压自动丢弃，不拖慢采集。
- 输出队列有界（默认 2）：状态引擎暂时忙时丢旧包，推理线程不阻塞。
- 标注帧回写邮箱供 API / MJPEG 使用。

独立运行
--------
检测器通过构造参数注入（真实 YOLO / Fake / DegradedDetector 均可）；
推理异常被内化为空检测包并计入 health()，线程不退出。
"""
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, List, Optional

from .capture import LatestFrameSlot
from .detector import Item, Person

logger = logging.getLogger(__name__)


@dataclass
class DetectionPacket:
    frame: object
    persons: List[Person]
    items: List[Item]
    frame_index: int
    captured_at: float
    inferred_at: float
    inference_ms: float
    error: str = ""
    bg_signals: Optional[dict] = None   # {seat_id: {"fg_ratio": x, "static": bool}}


class DetectionQueue:
    """有界检测包队列：满时丢最旧，get 支持超时"""

    def __init__(self, maxsize: int = 2):
        self.maxsize = max(1, int(maxsize))
        self._dq = deque()
        self._cond = threading.Condition()
        self._dropped = 0
        self._total = 0

    def put(self, packet: DetectionPacket) -> bool:
        with self._cond:
            dropped = False
            while len(self._dq) >= self.maxsize:
                self._dq.popleft()
                self._dropped += 1
                dropped = True
            self._dq.append(packet)
            self._total += 1
            self._cond.notify()
            return dropped

    def get(self, timeout: float = 0.5) -> Optional[DetectionPacket]:
        with self._cond:
            if not self._dq:
                self._cond.wait(timeout)
            if self._dq:
                return self._dq.popleft()
            return None

    def qsize(self) -> int:
        with self._cond:
            return len(self._dq)

    def stats(self) -> dict:
        with self._cond:
            return {"size": len(self._dq), "maxsize": self.maxsize,
                    "dropped": self._dropped, "total": self._total}


class InferenceWorker:
    """独立推理线程（检测器可注入，支持单步 tick 便于测试）"""

    def __init__(self, slot: LatestFrameSlot, out_queue: DetectionQueue,
                 detector, interval_sec: float = 1.0,
                 roi_provider: Optional[Callable[[], list]] = None,
                 annotated_sink: Optional[Callable[[object], None]] = None,
                 preprocessor=None, background=None,
                 name: str = "seat-inference"):
        self.slot = slot
        self.out_queue = out_queue
        self.detector = detector
        self.interval_sec = max(0.0, float(interval_sec))
        self.roi_provider = roi_provider
        self.preprocessor = preprocessor      # FramePreprocessor | None
        self.background = background          # BackgroundModel | None
        self.annotated_sink = annotated_sink or slot.set_annotated
        self.name = name
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_index = -1
        self._last_infer_at = 0.0
        self._status = {
            "inferred": 0,
            "skipped": 0,
            "last_frame_index": None,
            "last_infer_at": None,
            "last_inference_ms": None,
            "last_error": "",
        }

    # ── 生命周期 ─────────────────────────────────────────
    def start(self) -> bool:
        if self.is_alive():
            return False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()
        logger.info("推理线程已启动")
        return True

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        logger.info("推理线程已停止")

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── 主循环 ───────────────────────────────────────────
    def _run(self):
        while not self._stop.is_set():
            packet = self.tick()
            if packet is None:
                self._stop.wait(0.02)

    def tick(self, force: bool = False) -> Optional[DetectionPacket]:
        """处理一次最新帧；无新帧或未到节流间隔返回 None"""
        packet = self.slot.get()
        if packet is None:
            return None
        if packet.index == self._last_index:
            return None
        now = time.time()
        if not force and (now - self._last_infer_at) < self.interval_sec:
            self._status["skipped"] += 1
            return None

        self._last_index = packet.index
        self._last_infer_at = now
        persons: List[Person] = []
        items: List[Item] = []
        error = ""

        # 1) 帧预处理（缩放/光照均衡/去噪；异常时原样直通）
        frame = packet.frame
        if self.preprocessor is not None:
            try:
                frame = self.preprocessor.apply(frame)
            except Exception as e:
                self._status["last_error"] = f"预处理异常(已直通): {e}"

        # 2) 背景建模更新 + 每座位 ROI 前景信号（遗留物辅助证据）
        rois = self.roi_provider() if self.roi_provider else []
        bg_signals: Optional[dict] = None
        if self.background is not None:
            try:
                if self.background.update(frame) is not None:
                    bg_signals = {
                        roi["seat_id"]: self.background.region_signals(
                            roi["seat_id"], roi.get("polygon", []))
                        for roi in rois if roi.get("seat_id")
                    }
            except Exception as e:
                self._status["last_error"] = f"背景建模异常(已跳过): {e}"

        started = time.time()
        try:
            persons, items = self.detector.infer(frame)
        except Exception as e:
            # 兜底：注入的检测器若抛异常，也只降级为空结果
            error = f"{type(e).__name__}: {e}"
            self._status["last_error"] = error
            logger.warning("推理线程捕获异常(已降级为空结果): %s", e)

        try:
            annotated = self.detector.annotate(frame, persons, items, rois)
            self.annotated_sink(annotated)
        except Exception as e:
            self._status["last_error"] = f"标注失败: {e}"

        elapsed_ms = round((time.time() - started) * 1000, 1)
        out = DetectionPacket(
            frame=frame, persons=persons, items=items,
            frame_index=packet.index, captured_at=packet.captured_at,
            inferred_at=now, inference_ms=elapsed_ms, error=error,
            bg_signals=bg_signals)
        self.out_queue.put(out)
        self._status["inferred"] += 1
        self._status["last_frame_index"] = packet.index
        self._status["last_infer_at"] = now
        self._status["last_inference_ms"] = elapsed_ms
        return out

    def health(self) -> dict:
        with self._lock:
            st = dict(self._status)
        st["running"] = self.is_alive()
        st["queue"] = self.out_queue.stats()
        st["interval_sec"] = self.interval_sec
        st["detector"] = getattr(self.detector, "status", {})
        return st
