"""采集线程 —— 只负责"取帧"，不做任何推理与状态判定

通信接口
--------
采集线程 --写入--> LatestFrameSlot(最新帧邮箱) --读取--> 推理线程
邮箱容量恒为 1：新帧直接覆盖旧帧，读得慢时自动丢旧帧，采集永不阻塞推理。

数据流转
--------
cap.read() -> FramePacket(frame, index, captured_at, source) -> slot.put()
标注帧由推理线程回写 slot.set_annotated()，供 API / MJPEG 使用。

独立运行
--------
可脱离推理线程单独 start/stop；断流时指数退避重连，文件源循环播放；
全部异常内部消化并记录到 health()，线程本身不退出。
"""
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class FramePacket:
    frame: Any
    index: int
    captured_at: float
    source: str = ""
    meta: dict = field(default_factory=dict)


class LatestFrameSlot:
    """最新帧邮箱：容量 1，写入即覆盖，读取不阻塞写入"""

    def __init__(self):
        self._lock = threading.Lock()
        self._packet: Optional[FramePacket] = None
        self._annotated = None
        self._overwrites = 0

    def put(self, packet: FramePacket) -> bool:
        with self._lock:
            replaced = self._packet is not None
            if replaced:
                self._overwrites += 1
            self._packet = packet
            return replaced

    def get(self) -> Optional[FramePacket]:
        with self._lock:
            return self._packet

    def get_raw(self):
        with self._lock:
            return None if self._packet is None else self._packet.frame

    def set_annotated(self, frame):
        with self._lock:
            self._annotated = frame

    def get_annotated(self):
        with self._lock:
            return self._annotated

    def age(self) -> Optional[float]:
        with self._lock:
            if self._packet is None:
                return None
            return max(0.0, time.time() - self._packet.captured_at)

    def stats(self) -> dict:
        with self._lock:
            return {
                "has_frame": self._packet is not None,
                "frame_index": None if self._packet is None else self._packet.index,
                "overwrites": self._overwrites,
            }

    def clear(self):
        with self._lock:
            self._packet = None
            self._annotated = None


def _default_open(source: str):
    """默认视频源打开方式（惰性导入 cv2）"""
    try:
        import cv2
    except ImportError as e:
        raise RuntimeError("未安装 opencv-python，无法采集视频（pip install opencv-python）") from e
    cap = cv2.VideoCapture(int(source)) if str(source).isdigit() \
        else cv2.VideoCapture(str(source))
    if not cap.isOpened():
        cap.release()
        raise RuntimeError(f"视频源打开失败: {source}")
    return cap


class CaptureWorker:
    """独立采集线程

    opener: 可注入的视频源打开函数（测试用 FakeCap）
    """

    def __init__(self, source, slot: Optional[LatestFrameSlot] = None,
                 on_frame: Optional[Callable[[FramePacket], None]] = None,
                 opener: Optional[Callable[[str], Any]] = None,
                 is_file: Optional[bool] = None,
                 reconnect_initial: float = 2.0,
                 reconnect_max: float = 30.0,
                 name: str = "seat-capture"):
        self.source = str(source)
        self.slot = slot or LatestFrameSlot()
        self.on_frame = on_frame
        self._opener = opener or _default_open
        self.is_file = is_file if is_file is not None else self._guess_file()
        self.reconnect_initial = reconnect_initial
        self.reconnect_max = reconnect_max
        self.name = name
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._cap = None
        self._index = 0
        self._status = {
            "source": self.source,
            "is_file": self.is_file,
            "opened": False,
            "frames": 0,
            "read_failures": 0,
            "reconnects": 0,
            "last_frame_at": None,
            "last_error": "",
        }

    # ── 生命周期 ─────────────────────────────────────────
    def start(self) -> bool:
        if self.is_alive():
            return False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()
        logger.info("采集线程已启动: %s", self.source)
        return True

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        cap = self._cap
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._status["opened"] = False
        logger.info("采集线程已停止: %s", self.source)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── 主循环 ───────────────────────────────────────────
    def _run(self):
        delay = self.reconnect_initial
        while not self._stop.is_set():
            if self._cap is None:
                try:
                    self._cap = self._opener(self.source)
                    self._configure(self._cap)
                    self._status["opened"] = True
                    self._status["last_error"] = ""
                    delay = self.reconnect_initial
                    logger.info("视频源已接入: %s", self.source)
                except Exception as e:
                    self._status["last_error"] = f"视频源打开失败: {e}"
                    self._status["opened"] = False
                    logger.warning("%s（%.0fs 后重试）", self._status["last_error"], delay)
                    self._stop.wait(delay)
                    delay = min(self.reconnect_max, delay * 2)
                    self._status["reconnects"] += 1
                    continue

            try:
                ok, frame = self._cap.read()
            except Exception as e:
                ok, frame = False, None
                self._status["last_error"] = f"读取异常: {e}"

            if not ok or frame is None:
                self._status["read_failures"] += 1
                self._release_cap()
                if self.is_file and self._status["frames"] > 0:
                    # 文件源：回到起点循环播放（演示用）
                    self._cap = self._open_quiet()
                    continue
                if self._stop.is_set():
                    break
                self._status["reconnects"] += 1
                self._status["last_error"] = f"视频源断开: {self.source}"
                logger.warning("视频源断开，%.0fs 后重连: %s", delay, self.source)
                self._stop.wait(delay)
                delay = min(self.reconnect_max, delay * 2)
                self._cap = self._open_quiet()
                continue

            delay = self.reconnect_initial
            self._index += 1
            packet = FramePacket(frame=frame, index=self._index,
                                 captured_at=time.time(), source=self.source)
            self.slot.put(packet)
            self._status["frames"] = self._index
            self._status["last_frame_at"] = packet.captured_at
            if self.on_frame is not None:
                try:
                    self.on_frame(packet)
                except Exception as e:
                    self._status["last_error"] = f"帧回调异常: {e}"

            # 避免空转占满 CPU；读帧本身是阻塞的，这里只做极短让步
            if self.is_file:
                time.sleep(0.005)
        self._release_cap()

    def _open_quiet(self):
        try:
            cap = self._opener(self.source)
            self._configure(cap)
            self._status["opened"] = True
            return cap
        except Exception as e:
            self._status["last_error"] = f"视频源重连失败: {e}"
            self._status["opened"] = False
            return None

    def _release_cap(self):
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
        self._status["opened"] = False

    def _configure(self, cap):
        """设置缓冲/编码，失败不影响采集（部分后端不支持）"""
        try:
            import cv2
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if not self.is_file:
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        except Exception:
            pass

    def _guess_file(self) -> bool:
        s = self.source.lower()
        return not s.isdigit() and not s.startswith(("rtsp://", "http://", "https://"))

    def health(self) -> dict:
        st = dict(self._status)
        st["running"] = self.is_alive()
        st["slot"] = self.slot.stats()
        st["frame_age_sec"] = self.slot.age()
        return st
