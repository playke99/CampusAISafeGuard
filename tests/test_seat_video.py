"""视频采集占座监测增强测试

覆盖：采集稳定性(连续帧/断开重连)、帧预处理(缩放/光照均衡/直通降级)、
背景建模(帧差前景/静态遗留物判定)、状态机融合 bg 信号、监测 API 联调。
不依赖真实摄像头与 YOLO 权重：全部注入 Fake 组件，合成帧驱动。
"""
import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pytest
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from seat_monitor.background import BackgroundModel  # noqa: E402
from seat_monitor.capture import CaptureWorker  # noqa: E402
from seat_monitor.config import load_settings  # noqa: E402
from seat_monitor.detector import Item, Person  # noqa: E402
from seat_monitor.preprocess import FramePreprocessor  # noqa: E402
from seat_monitor.seat_state import SeatStateMachine  # noqa: E402

ROI = {"seat_id": "A01", "zone_name": "自习区A01",
       "polygon": [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]}
FRAME = np.zeros((480, 640, 3), dtype=np.uint8)


def wait_until(predicate, timeout=5.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


# ── 采集稳定性 ──────────────────────────────────────────────
class FakeCap:
    def __init__(self, frame=FRAME, fail_after=None):
        self.reads = 0
        self.frame = frame
        self.fail_after = fail_after

    def isOpened(self):
        return True

    def read(self):
        self.reads += 1
        if self.fail_after and self.reads > self.fail_after:
            return False, None
        return True, self.frame

    def set(self, *args):
        return True

    def release(self):
        pass


def test_capture_continuous_frames_no_gap():
    """连续采集：帧序号严格递增、时间戳单调、邮箱覆盖计数正确"""
    worker = CaptureWorker("0", opener=lambda src: FakeCap(),
                           reconnect_initial=0.02, reconnect_max=0.05)
    worker.start()
    try:
        assert wait_until(lambda: worker.health()["frames"] >= 30)
        h = worker.health()
        assert h["opened"] and h["running"] and not h["last_error"]
        slot_stats = h["slot"]
        assert slot_stats["frame_index"] >= 30
        assert slot_stats["overwrites"] >= 28   # 容量 1 邮箱，持续覆盖
        assert h["frame_age_sec"] is not None and h["frame_age_sec"] < 1.0
    finally:
        worker.stop()
    assert not worker.is_alive()


def test_capture_recovers_after_disconnect():
    """摄像头断开（read 失败）后指数退避重连并恢复帧流"""
    state = {"fail": True}

    class FlakyCap(FakeCap):
        def read(self):
            if state["fail"] and self.reads >= 3:
                return False, None
            return super().read()

    worker = CaptureWorker("0", opener=lambda src: FlakyCap(),
                           reconnect_initial=0.02, reconnect_max=0.05)
    worker.start()
    try:
        assert wait_until(lambda: worker.health()["reconnects"] >= 1)
        state["fail"] = False
        assert wait_until(lambda: worker.health()["frames"] >= 10)
        assert worker.health()["opened"]
    finally:
        worker.stop()


# ── 帧预处理 ────────────────────────────────────────────────
def test_preprocess_resize_and_stats():
    pp = FramePreprocessor({"resize_width": 320, "clahe": True})
    big = np.full((720, 1280, 3), 120, dtype=np.uint8)
    out = pp.apply(big)
    stats = pp.stats()
    if stats["cv2_available"]:
        assert out.shape[1] == 320            # 等比缩放到目标宽度
        assert stats["out_shape"][0] == 320
    else:
        assert out.shape == big.shape          # 无 cv2 时直通
    assert stats["processed"] + stats["fallbacks"] >= 1


def test_preprocess_no_resize_when_disabled_or_small():
    pp = FramePreprocessor({"resize_width": 0, "clahe": False})
    out = pp.apply(FRAME)
    assert out.shape == FRAME.shape


def test_preprocess_none_frame_safe():
    pp = FramePreprocessor({})
    assert pp.apply(None) is None


# ── 背景建模 ────────────────────────────────────────────────
def _bg(**over):
    cfg = {"method": "framediff", "history": 1000, "diff_threshold": 20,
           "blur": 0, "occupied_ratio": 0.10, "static_window_sec": 10,
           "static_stability": 0.06, "grid": 10}
    cfg.update(over)
    return BackgroundModel(cfg)


def test_background_learns_and_detects_foreground():
    """干净背景学习后，ROI 内出现静态物体 → 前景占比显著上升"""
    bg = _bg()
    base = np.full((240, 320, 3), 60, dtype=np.uint8)
    for _ in range(6):
        bg.update(base)
    r0 = bg.region_signals("A01", ROI["polygon"], now=100.0)
    assert r0["fg_ratio"] <= 0.05

    fg = base.copy()
    fg[60:170, 80:230] = 220                  # ROI 中心放一块亮块
    bg.update(fg)
    r1 = bg.region_signals("A01", ROI["polygon"], now=101.0)
    assert r1["fg_ratio"] >= 0.5              # 亮块占 ROI 大部分


def test_background_static_item_detection():
    """占比持续高于阈值且波动小 → static=True（遗留物）；占比归零 → static=False"""
    bg = _bg()
    base = np.full((240, 320, 3), 60, dtype=np.uint8)
    fg = base.copy()
    fg[60:170, 80:230] = 220
    for _ in range(6):
        bg.update(base)                        # 先学习干净背景
    ts = 200.0
    static_seen = False
    for i in range(8):
        bg.update(fg)                          # 同一前景帧，占比稳定
        sig = bg.region_signals("A01", ROI["polygon"], now=ts + i)
        static_seen = static_seen or sig["static"]
    assert static_seen

    for i in range(8, 16):
        bg.update(base)                        # 物体消失
        sig = bg.region_signals("A01", ROI["polygon"], now=ts + i)
    assert sig["static"] is False


def test_background_disabled_or_error_safe():
    bg = BackgroundModel({"enabled": False})
    assert bg.update(FRAME) is None
    assert bg.region_signals("A01", ROI["polygon"])["fg_ratio"] == 0.0
    bg2 = _bg()
    assert bg2.update(None) is None            # 异常输入不抛异常


# ── 状态机融合背景信号 ──────────────────────────────────────
class BgSettings:
    def __init__(self, **over):
        self.d = {
            "grace_seconds": 0,
            "absent_threshold_minutes": 0.00001,
            "absent_confirm_frames": 1,
            "require_items": True,
            "seat_thresholds": {},
            "camera_id": "test-cam",
            "background": {"assist_items": True},
        }
        self.d.update(over)

    def get(self, k):
        return self.d.get(k)


class FakeDetector:
    def __init__(self, items_enabled=True):
        self.items_enabled = items_enabled

    def infer(self, frame):
        return [], []

    def annotate(self, frame, persons, items, rois):
        return frame


def test_bg_static_item_drives_suspicion(tmp_path):
    """无检测物品时，背景"静态遗留物"补充证据：FREE→TIMING→SUSPECT(含 bg_ratio)"""
    machine = SeatStateMachine(FakeDetector(items_enabled=True),
                               BgSettings(), tmp_path / "snapshots")
    machine.sync_rois([ROI])
    sig = {"A01": {"fg_ratio": 0.33, "static": True}}

    events = machine.process(FRAME, [], [], bg_signals=sig)
    st = machine.seats["A01"]
    assert st.items == ["遗留物(背景)"]
    assert st.status.value == "timing" and not events

    time.sleep(0.02)
    events = machine.process(FRAME, [], [], bg_signals=sig)
    assert st.status.value == "suspect"
    assert len(events) == 1
    ev = events[0]
    assert ev["seat_id"] == "A01"
    assert ev["bg_ratio"] == 0.33
    assert "遗留物(背景)" in ev["items"]

    # 遗留物消失（背景占比回落）→ 状态回到空位
    gone = {"A01": {"fg_ratio": 0.01, "static": False}}
    machine.resolve_event(ev["event_id"], "清座")
    machine.process(FRAME, [], [], bg_signals=gone)
    assert st.items == []
    assert st.status.value == "free"


def test_bg_signal_off_keeps_free(tmp_path):
    """assist_items 关闭时，背景信号不影响判定"""
    machine = SeatStateMachine(FakeDetector(items_enabled=True),
                               BgSettings(background={"assist_items": False}),
                               tmp_path / "snapshots")
    machine.sync_rois([ROI])
    sig = {"A01": {"fg_ratio": 0.4, "static": True}}
    machine.process(FRAME, [], [], bg_signals=sig)
    st = machine.seats["A01"]
    assert st.items == []
    assert st.status.value == "free"
    assert st.last_bg_ratio == 0.4             # 信号仍被记录用于展示


def test_lighting_scenarios_recognition_stable(tmp_path):
    """多光照场景模拟：不同亮度帧下，检测输出一致 → 状态判定一致"""
    machine = SeatStateMachine(FakeDetector(items_enabled=True),
                               BgSettings(), tmp_path / "snapshots")
    machine.sync_rois([ROI])
    person = Person((280, 200, 360, 440), np.empty((0, 3)))
    bag = Item((300, 300, 340, 340), "背包", 0.8)
    for brightness in (40, 120, 230):          # 昏暗 / 正常 / 强光
        frame = np.full((480, 640, 3), brightness, dtype=np.uint8)
        machine.process(frame, [person], [bag])
        assert machine.seats["A01"].status.value == "occupied", f"亮度 {brightness} 误判"


# ── 监测 API 联调（真实 Uvicorn + 标准库客户端） ─────────────
PORT = 8271
BASE = f"http://127.0.0.1:{PORT}"


def _request(method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


@pytest.fixture(scope="module")
def monitor(tmp_path_factory):
    from seat_monitor.api import create_app
    from seat_monitor.main import MonitorContext

    base = tmp_path_factory.mktemp("seatmon")
    cfg_file = base / "settings.json"
    cfg_file.write_text(json.dumps({
        "source": "0", "camera_id": "test-cam", "detect_interval_sec": 0.05,
        "grace_seconds": 1, "absent_threshold_minutes": 25,
        "absent_confirm_frames": 1, "require_items": False,
        "preprocess": {"resize_width": 320, "clahe": True},
        "background": {"enabled": True, "method": "framediff",
                       "static_window_sec": 5, "history": 500},
        "http_port": PORT,
    }), encoding="utf-8")
    ctx = MonitorContext(load_settings(str(cfg_file)),
                         capture_opener=lambda src: FakeCap())
    ctx.pipeline.start_all()

    app = create_app(ctx)
    config = uvicorn.Config(app, host="127.0.0.1", port=PORT,
                            log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(60):
        try:
            status, _ = _request("GET", "/api/state")
            if status == 200:
                break
        except (urllib.error.URLError, ConnectionError):
            pass
        time.sleep(0.2)
    else:
        raise RuntimeError("监测服务启动超时")
    yield ctx, base
    server.should_exit = True
    thread.join(timeout=10)
    ctx.pipeline.stop_all()


def test_api_state_and_pipeline_running(monitor):
    status, body = _request("GET", "/api/state")
    assert status == 200
    assert body["camera_id"] == "test-cam"
    assert body["pipeline_running"]["capture"] is True
    # 标定 ROI 后座位出现在状态面板
    status, saved = _request("POST", "/api/rois", body=[ROI])
    assert status == 200 and saved[0]["seat_id"] == "A01"
    status, body = _request("GET", "/api/state")
    seats = {s["seat_id"]: s for s in body["seats"]}
    assert "A01" in seats and "bg_ratio" in seats["A01"]


def test_api_background_status(monitor):
    assert wait_until(lambda: _request("GET", "/api/background")[1]
                      .get("updates", 0) >= 2)
    status, body = _request("GET", "/api/background")
    assert status == 200
    assert body["backend"] == "framediff"
    assert body["enabled"] is True
    assert "seats" in body


def test_api_thresholds_global_and_per_seat(monitor):
    status, body = _request("POST", "/api/seat_thresholds",
                            body={"seat_id": "A01", "minutes": 15})
    assert status == 200 and body["seats"]["A01"] == 15
    status, body = _request("GET", "/api/seat_thresholds")
    assert body["seats"]["A01"] == 15 and body["default_minutes"] == 25
    # 清除覆盖
    status, body = _request("POST", "/api/seat_thresholds", body={"seat_id": "A01"})
    assert "A01" not in body["seats"]
    # 全局阈值修改
    status, body = _request("POST", "/api/settings",
                            body={"absent_threshold_minutes": 20})
    assert status == 200 and body["absent_threshold_minutes"] == 20


def test_api_events_filter_and_resolve(monitor):
    ctx, base = monitor
    events_log = base / "events.json"
    records = [
        {"event_id": "ev-a", "seat_id": "A01", "zone_name": "自习区A01",
         "occurred_at": "2026-11-12 09:00:00", "absent_minutes": 26,
         "items": ["背包"], "repeat_count": 1, "status": "待处理"},
        {"event_id": "ev-b", "seat_id": "A02", "zone_name": "自习区A02",
         "occurred_at": "2026-11-11 18:00:00", "absent_minutes": 31,
         "items": ["书本"], "repeat_count": 2, "status": "已处理(清座)"},
    ]
    events_log.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")

    status, body = _request("GET", "/api/events")
    assert status == 200 and body["total"] == 2
    status, body = _request("GET", "/api/events?seat_id=A01")
    assert body["total"] == 1 and body["records"][0]["event_id"] == "ev-a"
    status, body = _request("GET", "/api/events?status=" +
                            urllib.parse.quote("待处理"))
    assert body["total"] == 1
    status, body = _request("GET", "/api/events?date=2026-11-11")
    assert body["total"] == 1 and body["records"][0]["seat_id"] == "A02"
    status, body = _request("GET", "/api/events?q=" + urllib.parse.quote("背包"))
    assert body["total"] == 1

    # 处置闭环
    status, body = _request("POST", "/api/events/ev-a/resolve",
                            body={"action": "贴提示条"})
    assert status == 200
    status, body = _request("GET", "/api/events?status=" +
                            urllib.parse.quote("待处理"))
    assert body["total"] == 0
    # 非法动作
    status, _ = _request("POST", "/api/events/ev-b/resolve", body={"action": "X"})
    assert status == 400


def test_api_snapshot_endpoint(monitor):
    assert wait_until(lambda: monitor[0].get_frame(True) is not None)
    req = urllib.request.Request(BASE + "/api/snapshot.jpg?annotated=1")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status == 200:
                assert resp.headers.get("Content-Type") == "image/jpeg"
                assert len(resp.read()) > 100
            else:
                pytest.skip("当前环境无 cv2，快照编码不可用")
    except urllib.error.HTTPError as exc:
        if exc.code in (500, 503):
            pytest.skip("当前环境无 cv2，快照编码不可用")
        raise
