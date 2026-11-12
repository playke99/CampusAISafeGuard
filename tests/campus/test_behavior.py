"""计划4 单元测试：数据集校验、训练闭环、人脸匹配、违规事件。"""
import json
from pathlib import Path

from campus_ai.behavior_service.dataset import (split_dataset, validate_dataset,
                                                write_data_yaml)
from campus_ai.behavior_service.face_match import MockFaceMatcher
from campus_ai.behavior_service.inference import Detection, MockBehaviorDetector
from campus_ai.behavior_service.service import BehaviorViolationService
from campus_ai.behavior_service.train import train
from campus_ai.common.bus import InMemoryBus
from campus_ai.common.push_gateway import RecordingPushGateway
from campus_ai.scripts.make_demo_dataset import CLASSES, make_dataset


class _Dispatcher:
    def __init__(self):
        self.events = []

    def dispatch_event(self, event):
        self.events.append(event)
        return {"event_id": event.event_id, "targets": event.targets, "push_ok": True}


def test_demo_dataset_validate_split_and_yaml(tmp_path):
    root = make_dataset(str(tmp_path / "ds"), per_class=5)
    report = validate_dataset(root, CLASSES)
    assert report.ok and report.total_images == 10
    assert report.labeled_images == 10 and report.total_boxes == 10
    assert report.class_hist == {0: 5, 1: 5}

    split = split_dataset(root)
    assert len(split["train"]) + len(split["val"]) == 10
    yaml_path = write_data_yaml(root, CLASSES)
    data = json.loads(Path(yaml_path).read_text(encoding="utf-8"))
    assert data["names"] == {"0": "slippers", "1": "breakfast"}


def test_dataset_reports_invalid_label(tmp_path):
    root = tmp_path / "bad"
    (root / "images").mkdir(parents=True)
    (root / "labels").mkdir(parents=True)
    (root / "images" / "a.bmp").write_bytes(b"x")
    (root / "labels" / "a.txt").write_text("9 2.0 0.5 0.2 0.2\n", encoding="utf-8")
    report = validate_dataset(str(root), CLASSES)
    assert not report.ok
    assert any("越界" in e or "非法" in e for e in report.errors)


def test_train_dry_run(tmp_path):
    root = make_dataset(str(tmp_path / "ds2"), per_class=4)
    result = train(root, CLASSES, dry_run=True)
    assert result["status"] == "dry_run_ok"
    assert result["dataset_ok"] is True
    assert result["split"]["train"] + result["split"]["val"] == 8


def test_face_matcher_identified_and_unknown():
    matcher = MockFaceMatcher()
    known = matcher.match("2023001")
    assert known["student_id"] == "2023001" and known["major"] == "软件技术"
    unknown = matcher.match("nobody")
    assert unknown["student_id"] == "" and unknown["name"] == "unknown"


def test_behavior_service_events():
    dispatcher = _Dispatcher()
    bus = InMemoryBus()
    service = BehaviorViolationService(
        detector=MockBehaviorDetector([]), face_matcher=MockFaceMatcher(),
        agent=dispatcher, bus=bus, camera_id="teach-cam-01",
        location="教学楼A栋入口", targets=["discipline_office"])
    results = service.process_detections([
        Detection("slippers", 0.93, (1, 2, 3, 4), "2023001"),
        Detection("breakfast", 0.88, (5, 6, 7, 8), "nobody"),
    ])
    assert results[0]["identified"] is True
    assert results[0]["student"]["class_name"] == "软件2301"
    assert results[1]["needs_review"] is True
    assert len(dispatcher.events) == 2
    assert len(bus.published) == 2
