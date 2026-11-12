"""集成测试：四模块共享运行时，事件、推送、归档链路闭环。"""
from campus_ai.common.config import load_config
from campus_ai.common.push_gateway import RecordingPushGateway
from campus_ai.runtime import CampusRuntime
from campus_ai.scripts.make_demo_dataset import make_dataset


def _runtime(tmp_path):
    config = load_config()
    config["storage"]["root"] = str(tmp_path / "archive")
    config["intercom"]["danger_words_file"] = str(tmp_path / "danger_words.json")
    gateway = RecordingPushGateway()
    return CampusRuntime(config, gateway=gateway), gateway


def test_full_pipeline(tmp_path):
    runtime, gateway = _runtime(tmp_path)

    media = runtime.process_media({"transcript": "救命，有人打我",
                                   "location": "图书馆3层"})
    assert media["severity"] == "red"

    alert = runtime.handle_utterance({"transcript": "别打我"})
    assert alert and alert["severity"] == "orange"

    order = runtime.handle_seat_event({
        "seat_id": "A02", "zone_name": "自习区A02", "absent_minutes": 31,
        "items": ["书本"], "repeat_count": 2,
        "occurred_at": "2026-11-12 10:00:00"})
    assert order["orders"][0]["push_ok"] is True

    behavior = runtime.ingest_behavior({"detections": [
        {"label": "breakfast", "conf": 0.91, "bbox": [1, 2, 3, 4],
         "face_ref": "2023002"}]})
    assert behavior[0]["student"]["class_name"] == "大数据2302"

    assert runtime.stats()["reports"] >= 2
    assert runtime.alerts.count() == 1
    assert runtime.workorders.count() == 1
    assert runtime.violations.count() == 1
    assert gateway.pushed_to("library_staff")
    assert gateway.pushed_to("discipline_office")


def test_seat_work_order_escalates_on_repeat(tmp_path):
    runtime, _ = _runtime(tmp_path)
    first = runtime.handle_seat_event({"seat_id": "A03", "absent_minutes": 26,
                                       "items": ["背包"], "repeat_count": 1})
    second = runtime.handle_seat_event({"seat_id": "A03", "absent_minutes": 30,
                                        "items": ["背包"], "repeat_count": 2})
    assert first["event"]["severity"] == "yellow"
    assert second["event"]["severity"] == "orange"
    assert "第 2 次" in second["event"]["summary"]


def test_danger_words_hot_reload(tmp_path):
    runtime, _ = _runtime(tmp_path)
    assert runtime.handle_utterance({"transcript": "请勿大声喧哗"}) is None
    runtime.reload_danger_words({"red": ["大声喧哗"]})
    hit = runtime.handle_utterance({"transcript": "请勿大声喧哗"})
    assert hit and hit["severity"] == "red"
    assert (tmp_path / "archive").exists()
    assert runtime.danger_words() == {"red": ["大声喧哗"]}


def test_behavior_training_dry_run_via_runtime(tmp_path):
    runtime, _ = _runtime(tmp_path)
    dataset = make_dataset(str(tmp_path / "ds"), per_class=3)
    result = runtime.train_behavior({"data_root": dataset, "dry_run": True})
    assert result["status"] == "dry_run_ok"
    assert result["dataset_ok"] is True
