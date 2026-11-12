"""状态引擎线程 + 事件推送线程

状态引擎
--------
职责：消费 DetectionPacket，推进 SeatStateMachine，产出占座事件。
通信：DetectionQueue --读取--> StateEngineWorker --事件--> EventDispatcher
状态机只在状态引擎线程内被调用（单写者），API 只做只读快照，天然无锁竞争。

事件推送
--------
独立线程消费事件队列，调用 Agent(8100) 推送；推送慢/失败只会堆积在自己的
有界队列里，绝不阻塞状态引擎与推理线程。
"""
import logging
import queue
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class EventDispatcher:
    """独立事件推送线程（有界队列，失败不重试、不阻塞上游）"""

    def __init__(self, sink: Callable[[dict], bool], maxsize: int = 200,
                 name: str = "seat-event-push"):
        self.sink = sink
        self.name = name
        self._queue: "queue.Queue[dict]" = queue.Queue(maxsize=max(1, int(maxsize)))
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._status = {
            "pushed": 0,
            "failed": 0,
            "dropped": 0,
            "last_error": "",
            "last_push_at": None,
        }

    def start(self) -> bool:
        if self.is_alive():
            return False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()
        logger.info("事件推送线程已启动")
        return True

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        logger.info("事件推送线程已停止")

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def enqueue(self, event: dict) -> bool:
        try:
            self._queue.put_nowait(event)
            return True
        except queue.Full:
            self._status["dropped"] += 1
            logger.warning("推送队列已满，事件 %s 仅保留本地落盘",
                           event.get("event_id", "?"))
            return False

    def _run(self):
        while not self._stop.is_set():
            try:
                event = self._queue.get(timeout=0.3)
            except queue.Empty:
                continue
            try:
                ok = self.sink(event)
                if ok:
                    self._status["pushed"] += 1
                    import time
                    self._status["last_push_at"] = time.time()
                else:
                    self._status["failed"] += 1
            except Exception as e:
                self._status["failed"] += 1
                self._status["last_error"] = f"{type(e).__name__}: {e}"
                logger.warning("事件推送异常(本地已落盘): %s", e)

    def health(self) -> dict:
        st = dict(self._status)
        st["running"] = self.is_alive()
        st["queue_size"] = self._queue.qsize()
        return st


class StateEngineWorker:
    """独立状态引擎线程（状态机唯一写者）"""

    def __init__(self, machine, in_queue, event_sink: Optional[Callable[[dict], None]] = None,
                 name: str = "seat-state-engine"):
        self.machine = machine
        self.in_queue = in_queue
        self.event_sink = event_sink
        self.name = name
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._status = {
            "processed": 0,
            "events_generated": 0,
            "last_process_at": None,
            "last_frame_index": None,
            "last_error": "",
        }

    def start(self) -> bool:
        if self.is_alive():
            return False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()
        logger.info("状态引擎线程已启动")
        return True

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        logger.info("状态引擎线程已停止")

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self):
        while not self._stop.is_set():
            packet = self.in_queue.get(timeout=0.3)
            if packet is None:
                continue
            self.process(packet)

    def process(self, packet) -> list:
        """处理一个检测包；状态机异常被隔离，不终止线程"""
        import time
        try:
            events = self.machine.process(packet.frame, packet.persons,
                                          packet.items,
                                          bg_signals=getattr(packet, "bg_signals", None))
        except Exception as e:
            self._status["last_error"] = f"{type(e).__name__}: {e}"
            logger.exception("状态引擎处理异常(已跳过该帧)")
            return []
        self._status["processed"] += 1
        self._status["last_process_at"] = time.time()
        self._status["last_frame_index"] = getattr(packet, "frame_index", None)
        for event in events or []:
            self._status["events_generated"] += 1
            if self.event_sink is not None:
                try:
                    self.event_sink(event)
                except Exception as e:
                    self._status["last_error"] = f"事件分发异常: {e}"
        return events or []

    def health(self) -> dict:
        st = dict(self._status)
        st["running"] = self.is_alive()
        st["queue"] = self.in_queue.stats()
        return st
