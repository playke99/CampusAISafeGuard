"""统一后端 API 集成测试：真实启动 Uvicorn，使用标准库 HTTP 客户端。"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest
import uvicorn

from server.api import create_app
from server.core.config import load_config

PORT = 8123
BASE = f"http://127.0.0.1:{PORT}"


def _request(method: str, path: str, token: str | None = None,
             body: dict | None = None, headers: dict | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _login(username: str, password: str) -> str:
    status, body = _request("POST", "/api/v1/auth/login",
                            body={"username": username, "password": password})
    assert status == 200 and body["code"] == 0, body
    return body["data"]["token"]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    cfg = load_config()
    cfg["app"]["db_path"] = str(tmp_path_factory.mktemp("db") / "test.db")
    cfg["app"]["port"] = PORT
    app = create_app(cfg)
    config = uvicorn.Config(app, host="127.0.0.1", port=PORT,
                            log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(60):
        try:
            status, body = _request("GET", "/api/v1/health")
            if status == 200:
                break
        except (urllib.error.URLError, ConnectionError):
            pass
        time.sleep(0.2)
    else:
        raise RuntimeError("测试服务启动超时")
    yield
    server.should_exit = True
    thread.join(timeout=10)


def test_health(server):
    status, body = _request("GET", "/api/v1/health")
    assert status == 200
    assert body["data"]["runtime"] is True


def test_auth_success_and_failure(server):
    token = _login("admin", "admin123")
    status, body = _request("GET", "/api/v1/auth/me", token=token)
    assert status == 200 and body["data"]["role"] == "admin"

    status, body = _request("POST", "/api/v1/auth/login",
                            body={"username": "admin", "password": "bad"})
    assert status == 401 and body["code"] == 40100


def test_alert_closed_loop(server):
    token = _login("admin", "admin123")
    status, body = _request("POST", "/api/v1/scenarios/vision_bullying",
                            token=token)
    assert status == 200
    alerts = body["data"]["alerts"]
    assert len(alerts) == 1 and alerts[0]["severity"] == "red"
    alert_id = alerts[0]["alert_id"]

    status, body = _request("POST", f"/api/v1/alerts/{alert_id}/ack", token=token)
    assert body["data"]["status"] == "acknowledged"

    status, body = _request("POST", f"/api/v1/alerts/{alert_id}/intervene",
                            token=token,
                            body={"kind": "dispatch", "content": "已派员"})
    assert body["data"]["status"] == "intervening"

    status, body = _request("POST", f"/api/v1/alerts/{alert_id}/close",
                            token=token, body={"result": "处置完成"})
    assert body["data"]["status"] == "closed"

    status, body = _request("GET", f"/api/v1/alerts/{alert_id}", token=token)
    assert body["data"]["status"] == "closed"
    assert len(body["data"]["interventions"]) >= 4
    assert "处置建议" in body["data"]["report_text"]


def test_rbac_viewer_cannot_run_scenario(server):
    token = _login("viewer", "viewer123")
    status, body = _request("POST", "/api/v1/scenarios/vision_attack", token=token)
    assert status == 403 and body["code"] == 40300
    status, body = _request("GET", "/api/v1/events", token=token)
    assert status == 200


def test_vision_detect_dedupe(server):
    token = _login("security", "security123")
    from demo import pose_builder
    frames, _, _, _ = pose_builder.build("attack")
    payload = {"camera_id": "test-cam-dedupe", "location": "测试走廊",
               "frames": frames}
    status, first = _request("POST", "/api/v1/vision/detect", token=token,
                             body=payload)
    assert status == 200 and first["data"]["count"] == 1
    status, second = _request("POST", "/api/v1/vision/detect", token=token,
                              body=payload)
    assert second["data"]["count"] == 0
    payload["force"] = True
    status, third = _request("POST", "/api/v1/vision/detect", token=token,
                             body=payload)
    assert third["data"]["count"] == 1


def test_seat_extension_and_order_close(server):
    token = _login("admin", "admin123")
    _request("GET", "/api/v1/health")
    status, body = _request(
        "POST", "/api/seat_event",
        body={"seat_id": "T-901", "zone_name": "图书馆测试区",
              "absent_minutes": 40, "items": ["书包"], "repeat_count": 1},
        headers={"X-Integration-Token": "campus-local"})
    assert status == 200 and body["orders"]
    order_id = body["orders"][0]["order_id"]

    status, body = _request("GET", "/api/v1/orders", token=token)
    assert any(row["event_id"] == order_id for row in body["data"]["records"])

    status, body = _request("POST", f"/api/v1/orders/{order_id}/close",
                            token=token)
    assert body["data"]["status"] == "closed"


def test_seat_extension_rejects_bad_token(server):
    status, body = _request("POST", "/api/seat_event",
                            body={"seat_id": "T-902", "absent_minutes": 30},
                            headers={"X-Integration-Token": "wrong"})
    assert status == 403
