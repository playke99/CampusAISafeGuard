"""公共层单元测试：配置、事件、总线、推送、归档。"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from campus_ai.common.archive import JsonlArchive
from campus_ai.common.bus import InMemoryBus, TOPIC_ALERT
from campus_ai.common.config import DEFAULTS, load_config
from campus_ai.common.events import EventType, Severity, make_event
from campus_ai.common.push_gateway import (HttpPushGateway, JsonlPushGateway,
                                           RecordingPushGateway)


def _event(severity=Severity.YELLOW, targets=None):
    return make_event(EventType.ALERT, severity, "unit", "测试点", "单元测试事件",
                      targets=targets or [])


def test_config_deep_merge_does_not_pollute_defaults(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"agent": {"model": "custom"}}), encoding="utf-8")
    config = load_config(str(path))
    assert config["agent"]["model"] == "custom"
    assert config["agent"]["route"]["red"]  # 默认值保留
    config["agent"]["route"]["red"].append("mutated")
    assert "mutated" not in DEFAULTS["agent"]["route"]["red"]


def test_event_ids_unique_and_serializable():
    first, second = _event(), _event()
    assert first.event_id != second.event_id
    data = first.to_dict()
    assert data["event_type"] == "alert"
    assert data["severity"] == "yellow"
    json.dumps(data, ensure_ascii=False)


def test_severity_ranking():
    assert (Severity.RED.rank > Severity.ORANGE.rank >
            Severity.YELLOW.rank > Severity.BLUE.rank)


def test_bus_publish_subscribe_and_filter():
    bus = InMemoryBus()
    received = []
    bus.subscribe(TOPIC_ALERT, received.append)
    event = _event()
    bus.publish(TOPIC_ALERT, event)
    bus.publish("other.topic", _event(Severity.RED))
    assert received == [event]
    assert bus.events_of(TOPIC_ALERT) == [event]


def test_recording_gateway_retry_semantics():
    gateway = RecordingPushGateway(fail_times=1)
    event = _event(targets=["library_staff"])
    assert gateway.push(event, event.targets)["ok"] is False
    assert gateway.push(event, event.targets)["ok"] is True
    assert gateway.pushed_to("library_staff")[0]["event_id"] == event.event_id


def test_jsonl_push_gateway_and_archive(tmp_path):
    gateway = JsonlPushGateway(tmp_path / "queue.jsonl")
    event = _event(targets=["staff"])
    assert gateway.push(event, event.targets)["ok"] is True
    lines = (tmp_path / "queue.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["targets"] == ["staff"]

    archive = JsonlArchive(tmp_path / "archive.jsonl")
    archive.append({"n": 1})
    archive.append({"n": 2})
    assert archive.count() == 2
    assert archive.list(limit=1) == [{"n": 2}]


def test_http_push_gateway_posts_event():
    received: list[bytes] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            received.append(self.rfile.read(length))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        gateway = HttpPushGateway(
            f"http://127.0.0.1:{server.server_address[1]}/push",
            timeout=1, max_retry=2)
        event = _event(targets=["staff"])
        assert gateway.push(event, event.targets)["ok"] is True
        payload = json.loads(received[0].decode("utf-8"))
        assert payload["targets"] == ["staff"]
        assert payload["event"]["event_id"] == event.event_id
    finally:
        server.shutdown()
