"""三线程解耦测试：独立启停、互不阻塞、队列丢旧、事件推送隔离"""
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from seat_monitor.capture import CaptureWorker, FramePacket, LatestFrameSlot  # noqa: E402
from seat_monitor.detector import Person  # noqa: E402
from seat_monitor.inference import DetectionQueue, DetectionPacket, InferenceWorker  # noqa: E402
from seat_monitor.pipeline import build_pipeline  # noqa: E402
from seat_monitor.seat_state import SeatStateMachine  # noqa: E402
from seat_monitor.state_engine import EventDispatcher, StateEngineWorker  # noqa: E402

ROI = {"seat_id": "A01", "zone_name": "自习区A01",
       "polygon": [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]}
FRAME = np.zeros((480, 640, 3), dtype=np.uint8)


class FakeSettings:
    def __init__(self, **override):
        self.d = {
            "source": "0",
            "camera_id": "test-cam",
            "agent_url": "http://127.0.0.1:9",
            "detect_interval_sec": 0.0,
            "grace_seconds": 1,
            "absent_threshold_minutes": 1,
            "absent_confirm_frames": 1,
            "require_items": False,
            "seat_thresholds": {},
        }
        self.d.update(override)

    def get(self, key):
        return self.d.get(key)


class FakeDetector:
    """人始终在 ROI 中心，且推理可统计调用次数"""
    items_enabled = False
    status = {"mode": "pose", "mode_cn": "测试", "degraded": False, "message": ""}

    def __init__(self):
        self.calls = 0

    def infer(self, frame):
        self.calls += 1
        person = Person((280, 200, 360, 440), np.empty((0, 3)))
        return [person], []

    def annotate(self, frame, persons, items, rois):
        return frame


class FakeCap:
    def __init__(self):
        self.reads = 0
        self.released = False

    def isOpened(self):
        return True

    def read(self):
        self.reads += 1
        time.sleep(0.01)
        return True, np.zeros((480, 640, 3), dtype=np.uint8)

    def set(self, *args):
        return True

    def release(self):
        self.released = True


def wait_until(predicate, timeout=3.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def make_machine(tmp_path):
    settings = FakeSettings()
    machine = SeatStateMachine(FakeDetector(), settings, tmp_path / "snapshots")
    machine.sync_rois([ROI])
    return machine, settings


def make_packet(index=1, persons=None):
    return DetectionPacket(frame=FRAME, persons=persons or [], items=[], frame_index=index,
                           captured_at=time.time(), inferred_at=time.time(),
                           inference_ms=0.0)


# ── 传输层：最新帧邮箱 / 有界检测队列 ─────────────────────
def test_latest_frame_slot_keeps_only_newest():
    slot = LatestFrameSlot()
    for i in range(1, 6):
        slot.put(FramePacket(FRAME, i, time.time()))
    assert slot.get().index == 5
    assert slot.stats()["overwrites"] == 4


def test_roi_store_replace_does_not_deadlock(tmp_path):
    from seat_monitor.roi_store import RoiStore
    store = RoiStore(tmp_path / "rois.json")
    saved = store.replace_all([ROI])
    assert saved[0]["seat_id"] == "A01"
    assert store.all()[0]["seat_id"] == "A01"


def test_detection_queue_drops_oldest_when_full():
    q = DetectionQueue(maxsize=2)
    for i in range(1, 5):
        q.put(make_packet(i))
    stats = q.stats()
    assert stats["size"] == 2 and stats["dropped"] == 2
    assert q.get().frame_index == 3
    assert q.get().frame_index == 4


# ── 推理线程可脱离采集线程单独工作 ────────────────────────
def test_inference_worker_ticks_without_capture(tmp_path):
    slot, q = LatestFrameSlot(), DetectionQueue(maxsize=2)
    detector = FakeDetector()
    worker = InferenceWorker(slot, q, detector, interval_sec=0)
    slot.put(FramePacket(FRAME, 1, time.time()))
    packet = worker.tick(force=True)
    assert packet is not None and packet.frame_index == 1
    assert q.qsize() == 1 and detector.calls == 1
    # 同一帧不重复推理
    assert worker.tick(force=True) is None


# ── 状态引擎可脱离上游单独消费 ────────────────────────────
def test_state_engine_consumes_queue_alone(tmp_path):
    machine, _ = make_machine(tmp_path)
    q = DetectionQueue(maxsize=2)
    engine = StateEngineWorker(machine, q)
    engine.start()
    try:
        q.put(make_packet(1, persons=[Person((280, 200, 360, 440), np.empty((0, 3)))]))
        assert wait_until(lambda: engine.health()["processed"] >= 1)
        assert machine.seats["A01"].status.value == "occupied"
    finally:
        engine.stop()


# ── 三模块独立启停、互不阻塞 ──────────────────────────────
def test_pipeline_modules_run_and_stop_independently(tmp_path):
    machine, settings = make_machine(tmp_path)
    slot = LatestFrameSlot()
    detector = FakeDetector()
    pushed = []
    pipeline = build_pipeline(settings, machine, detector, slot=slot,
                              roi_provider=lambda: [ROI],
                              push_sink=lambda ev: pushed.append(ev) or True,
                              capture_opener=lambda src: FakeCap())

    # 只启动推理 + 状态引擎
    assert pipeline.start("inference") is True
    assert pipeline.start("state_engine") is True
    assert pipeline.is_running("inference") and pipeline.is_running("state_engine")
    assert not pipeline.is_running("capture")

    slot.put(FramePacket(FRAME, 1, time.time()))
    assert wait_until(lambda: pipeline.engine.health()["processed"] >= 1)

    # 再单独启动采集：帧/推理计数增长
    assert pipeline.start("capture") is True
    assert wait_until(lambda: pipeline.capture.health()["frames"] >= 2)
    assert wait_until(lambda: pipeline.inference.health()["inferred"] >= 2)

    # 停止采集：推理与状态引擎继续运行，不退出
    pipeline.stop("capture")
    assert not pipeline.is_running("capture")
    assert pipeline.is_running("inference") and pipeline.is_running("state_engine")

    # 推理停止后，状态引擎仍可消费手工投递的检测包
    pipeline.stop("inference")
    before = pipeline.engine.health()["processed"]
    pipeline.engine.in_queue.put(make_packet(99))
    assert wait_until(lambda: pipeline.engine.health()["processed"] > before)

    pipeline.stop_all()
    assert all(not pipeline.is_running(n) for n in
               ("capture", "inference", "state_engine", "event_dispatcher"))


def test_capture_survives_open_failure():
    worker = CaptureWorker("0", opener=lambda src: (_ for _ in ()).throw(
        RuntimeError("设备被占用")), reconnect_initial=0.02, reconnect_max=0.04)
    worker.start()
    try:
        assert wait_until(lambda: worker.health()["reconnects"] >= 1)
        assert worker.is_alive()
        assert "设备被占用" in worker.health()["last_error"]
    finally:
        worker.stop()


# ── 事件推送线程隔离 ─────────────────────────────────────
def test_dispatcher_never_blocks_producer():
    def slow_sink(event):
        time.sleep(0.3)
        return True

    dispatcher = EventDispatcher(slow_sink, maxsize=10)
    dispatcher.start()
    try:
        started = time.time()
        for i in range(5):
            dispatcher.enqueue({"event_id": f"e{i}"})
        assert time.time() - started < 0.15        # 入队不等待慢推送
        assert wait_until(lambda: dispatcher.health()["pushed"] >= 1, timeout=2)
    finally:
        dispatcher.stop()


class StubMachine:
    def process(self, frame, persons, items, bg_signals=None):
        return [{"event_id": "ev-1", "seat_id": "A01"}]


def test_state_engine_forwards_events_to_dispatcher():
    q = DetectionQueue(maxsize=2)
    seen = []
    dispatcher = EventDispatcher(lambda ev: seen.append(ev) or True)
    dispatcher.start()
    engine = StateEngineWorker(StubMachine(), q, event_sink=dispatcher.enqueue)
    try:
        q.put(make_packet(1))
        engine.process(q.get())
        assert wait_until(lambda: len(seen) == 1)
        assert engine.health()["events_generated"] == 1
    finally:
        dispatcher.stop()


def test_pipeline_health_schema(tmp_path):
    machine, settings = make_machine(tmp_path)
    pipeline = build_pipeline(settings, machine, FakeDetector(),
                              push_sink=lambda ev: True)
    health = pipeline.health()
    assert set(health["running"]) == {"capture", "inference",
                                      "state_engine", "event_dispatcher"}
    assert health["queues"]["detections"]["size"] == 0
    assert health["detector"]["mode"] == "pose"
    assert health["degraded"] is False
    assert "server_time" in health
