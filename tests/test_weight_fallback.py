"""权重合法性校验与三级降级测试（不依赖 cv2 / ultralytics）"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from seat_monitor.detector import (  # noqa: E402
    DegradedDetector, Detector, build_detector, validate_weight,
)


class FakeModel:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = 0

    def predict(self, frame, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("模型推理崩溃")
        return [type("R", (), {"boxes": None, "keypoints": None})()]


class FakeLoader:
    def __init__(self, results):
        """results: path 子串 -> 模型实例或异常"""
        self.results = results
        self.loaded = []

    def __call__(self, path):
        self.loaded.append(path)
        for key, value in self.results.items():
            if key in str(path):
                if isinstance(value, Exception):
                    raise value
                return value
        raise AssertionError(f"未预期的权重路径: {path}")


def _weight(tmp_path: Path, name: str, size: int = 4096) -> Path:
    p = tmp_path / name
    p.write_bytes(b"\0" * size)
    return p


# ── validate_weight ─────────────────────────────────────
def test_validate_weight_missing(tmp_path):
    info = validate_weight(tmp_path / "nope.pt")
    assert info["exists"] is False and info["valid"] is False
    assert "不存在" in info["error"]


def test_validate_weight_directory(tmp_path):
    info = validate_weight(tmp_path)
    assert info["valid"] is False
    assert "不是文件" in info["error"]


def test_validate_weight_empty_file(tmp_path):
    p = tmp_path / "empty.pt"
    p.write_bytes(b"")
    info = validate_weight(p)
    assert info["exists"] is True and info["valid"] is False
    assert "损坏" in info["error"] or "为空" in info["error"]


def test_validate_weight_ok(tmp_path):
    info = validate_weight(_weight(tmp_path, "ok.pt"))
    assert info["valid"] is True and info["size_bytes"] == 4096


# ── build_detector 三级策略 ──────────────────────────────
def test_pose_loaded_primary_mode(tmp_path):
    pose = _weight(tmp_path, "pose.pt")
    detect = _weight(tmp_path, "detect.pt")
    loader = FakeLoader({"pose.pt": FakeModel(), "detect.pt": FakeModel()})
    det = build_detector(pose, detect, loader=loader)
    assert isinstance(det, Detector)
    assert det.status["mode"] == "pose"
    assert det.status["degraded"] is False
    assert det.items_enabled is True


def test_pose_missing_fallback_to_detect(tmp_path):
    detect = _weight(tmp_path, "detect.pt")
    loader = FakeLoader({"detect.pt": FakeModel()})
    det = build_detector(tmp_path / "missing.pt", detect, loader=loader)
    assert isinstance(det, Detector)
    assert det.status["mode"] == "fallback"
    assert det.status["degraded"] is True
    assert det.items_enabled is True
    assert "降级" in det.status["message"]


def test_pose_load_error_fallback(tmp_path):
    pose = _weight(tmp_path, "pose.pt")
    detect = _weight(tmp_path, "detect.pt")
    loader = FakeLoader({"pose.pt": ValueError("权重损坏"),
                         "detect.pt": FakeModel()})
    det = build_detector(pose, detect, loader=loader)
    assert det.status["mode"] == "fallback"
    assert "姿态权重加载失败" in det.status["pose"]["error"]


def test_all_weights_broken_degraded(tmp_path):
    pose = _weight(tmp_path, "pose.pt")
    loader = FakeLoader({"pose.pt": OSError("无法反序列化")})
    det = build_detector(pose, tmp_path / "missing.pt", loader=loader)
    assert isinstance(det, DegradedDetector)
    assert det.status["mode"] == "degraded"
    assert det.status["degraded"] is True
    assert det.items_enabled is False
    # 降级检测器不抛异常，返回空结果
    persons, items = det.infer(np.zeros((8, 8, 3), dtype=np.uint8))
    assert persons == [] and items == []


def test_zero_size_weight_never_loaded(tmp_path):
    pose = tmp_path / "pose.pt"
    pose.write_bytes(b"")
    loader = FakeLoader({})
    det = build_detector(pose, None, loader=loader)
    assert isinstance(det, DegradedDetector)
    assert loader.loaded == []          # 损坏文件不进入加载流程
    assert "损坏" in det.status["pose"]["error"] or "为空" in det.status["pose"]["error"]


# ── 运行时异常隔离 ───────────────────────────────────────
def test_infer_exception_is_contained():
    model = FakeModel(fail=True)
    det = Detector(model, None, pose_mode=True)
    persons, items = det.infer(np.zeros((8, 8, 3), dtype=np.uint8))
    assert persons == [] and items == []
    assert det.status["infer_errors"] == 1
    assert "模型推理崩溃" in det.status["last_error"]


def test_degraded_annotate_returns_frame():
    det = DegradedDetector("test")
    frame = np.zeros((4, 4, 3), dtype=np.uint8)
    assert det.annotate(frame, [], [], []) is frame
