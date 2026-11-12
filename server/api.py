"""REST API：认证、态势总览、预警闭环、视觉检测、场景演示与后台管理。

统一响应：成功 {"code":0,"data":...}；失败 {"code":xxxxx,"error":"..."}。
权限：Authorization: Bearer <token>，接口级权限点校验。
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Any

from fastapi import Depends, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ai.alert_engine import AlertEngine, TYPE_LABELS, new_event_id
from ai.llm.analyzer import SEVERITY_LABELS
from demo.scenarios import ScenarioRunner
from server.core import auth, db
from server.core.config import db_path, load_config, resolve_path
from server.core.metrics import METRICS

STATUS_LABELS = {"pending": "待受理", "acknowledged": "已受理",
                 "intervening": "干预中", "closed": "已闭环"}
_CODE_STATUS = {40000: 400, 40100: 401, 40300: 403, 40400: 404,
                40500: 405, 40900: 409, 50000: 500}

# 登录防爆破
_LOGIN_FAILS: dict[str, list] = {}
_LOCK_THRESHOLD, _LOCK_SECONDS = 5, 300


class ApiError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def ok(data: Any) -> dict[str, Any]:
    return {"code": 0, "data": data}


def _token(request: Request) -> str:
    value = request.headers.get("Authorization", "")
    return value[7:] if value.startswith("Bearer ") else ""


def _serialize_alert(row: dict[str, Any]) -> dict[str, Any]:
    evidence = row.get("evidence") or {}
    if not isinstance(evidence, dict):
        try:
            evidence = json.loads(evidence)
        except (TypeError, ValueError):
            evidence = {}
    row["evidence"] = evidence
    row["severity_label"] = SEVERITY_LABELS.get(row.get("severity", ""), "")
    row["type_label"] = TYPE_LABELS.get(row.get("alert_type", ""), "")
    row["status_label"] = STATUS_LABELS.get(row.get("status", ""), "")
    row["occurred_text"] = datetime.fromtimestamp(
        row.get("occurred_at", 0)).strftime("%Y-%m-%d %H:%M:%S")
    return row


def create_app(config: dict[str, Any] | None = None,
               runtime: Any | None = None) -> FastAPI:
    cfg = config or load_config()
    database = db_path(cfg)
    db.init_db(database, seed=True)

    engine = AlertEngine(cfg)
    if runtime is None:
        try:
            from campus_ai.runtime import CampusRuntime
            runtime = CampusRuntime(
                config_path=resolve_path(cfg["campus_ai"]["config_path"]))
        except Exception as exc:  # noqa: BLE001 - 运行时不可用不影响主服务
            import logging
            logging.getLogger("campus").warning("campus_ai 运行时不可用: %s", exc)
            runtime = None
    runner = ScenarioRunner(cfg, engine, runtime)

    app = FastAPI(title=cfg["app"]["name"], version=cfg["app"]["version"],
                  docs_url="/api/docs", redoc_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                       allow_headers=["*"])
    app.state.config = cfg
    app.state.runtime = runtime

    # ---------- 依赖 ----------
    def get_conn():
        conn = db.connect(database)
        try:
            yield conn
        finally:
            conn.close()

    def require(perm: str | None = None):
        def dep(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
            user = auth.resolve_session(conn, _token(request))
            if not user:
                raise ApiError(40100, "未登录或登录已过期")
            if perm and not auth.has_permission(user["role"], perm):
                raise ApiError(40300, f"无权限执行该操作（需要 {perm}）")
            return user
        return dep

    @app.exception_handler(ApiError)
    def _handle(request: Request, exc: ApiError):
        return JSONResponse(status_code=_CODE_STATUS.get(exc.code, 400),
                            content={"code": exc.code, "error": exc.message})

    # ---------- 健康检查 ----------
    @app.get("/api/v1/health")
    def health():
        return ok({"status": "up", "time": time.time(),
                   "version": cfg["app"]["version"],
                   "runtime": bool(runtime)})

    # ---------- 认证 ----------
    @app.post("/api/v1/auth/login")
    def login(body: dict[str, Any], conn: sqlite3.Connection = Depends(get_conn)):
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", ""))
        if not username or not password:
            raise ApiError(40000, "用户名与密码不能为空")
        record = _LOGIN_FAILS.get(username)
        if record and record[1] > time.time():
            raise ApiError(40300, "失败次数过多，账号已临时锁定，请 5 分钟后重试")
        row = conn.execute(
            "SELECT username,password_hash,name,role,enabled FROM users "
            "WHERE username=?", (username,)).fetchone()
        if not row or not row["enabled"] or not auth.verify_password(
                password, row["password_hash"]):
            fails, lock_until = _LOGIN_FAILS.get(username, [0, 0.0])
            fails += 1
            if fails >= _LOCK_THRESHOLD:
                fails, lock_until = 0, time.time() + _LOCK_SECONDS
            _LOGIN_FAILS[username] = [fails, lock_until]
            METRICS.incr("auth.login_fail")
            raise ApiError(40100, "用户名或密码错误")
        _LOGIN_FAILS.pop(username, None)
        token = auth.create_session(conn, username, row["role"],
                                    int(cfg["app"]["token_ttl_sec"]))
        db.audit(conn, username, "login", "登录成功")
        METRICS.incr("auth.login_ok")
        return ok({"token": token,
                   "user": {"username": username, "name": row["name"],
                            "role": row["role"],
                            "role_label": auth.ROLE_LABELS.get(row["role"], ""),
                            "permissions": auth.permissions_of(row["role"])}})

    @app.post("/api/v1/auth/logout")
    def logout(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
        token = _token(request)
        if token:
            auth.drop_session(conn, token)
        return ok({"message": "已退出"})

    @app.get("/api/v1/auth/me")
    def me(user: dict = Depends(require()),
           conn: sqlite3.Connection = Depends(get_conn)):
        row = conn.execute("SELECT name FROM users WHERE username=?",
                           (user["username"],)).fetchone()
        return ok({"username": user["username"], "role": user["role"],
                   "name": row["name"] if row else user["username"],
                   "role_label": auth.ROLE_LABELS.get(user["role"], ""),
                   "permissions": auth.permissions_of(user["role"])})

    # ---------- 态势总览 ----------
    @app.get("/api/v1/overview")
    def overview(user: dict = Depends(require("event.view")),
                 conn: sqlite3.Connection = Depends(get_conn)):
        now = time.time()
        today_start = datetime.now().replace(hour=0, minute=0, second=0,
                                             microsecond=0).timestamp()
        stats = {
            "alerts_total": db.scalar(conn, "SELECT COUNT(*) FROM alerts"),
            "alerts_today": db.scalar(
                conn, "SELECT COUNT(*) FROM alerts WHERE occurred_at>=?",
                (today_start,)),
            "pending": db.scalar(
                conn, "SELECT COUNT(*) FROM alerts WHERE status!='closed'"),
            "red_open": db.scalar(
                conn, "SELECT COUNT(*) FROM alerts WHERE severity='red' "
                      "AND status!='closed'"),
            "interventions": db.scalar(conn, "SELECT COUNT(*) FROM interventions"),
            "events_today": db.scalar(
                conn, "SELECT COUNT(*) FROM events WHERE created_at>=?",
                (today_start,)),
            "orders_pending": db.scalar(
                conn, "SELECT COUNT(*) FROM work_orders WHERE status='pending'"),
            "devices_online": db.scalar(
                conn, "SELECT COUNT(*) FROM devices WHERE status='online'"),
            "devices_total": db.scalar(conn, "SELECT COUNT(*) FROM devices"),
            "violations": db.scalar(conn, "SELECT COUNT(*) FROM violations"),
        }
        severity = {s: 0 for s in ("red", "orange", "yellow", "blue")}
        for row in db.query(conn,
                            "SELECT severity,COUNT(*) c FROM alerts "
                            "GROUP BY severity"):
            severity[row["severity"]] = row["c"]
        types = {k: 0 for k in TYPE_LABELS}
        for row in db.query(conn,
                            "SELECT alert_type,COUNT(*) c FROM alerts "
                            "GROUP BY alert_type"):
            types[row["alert_type"]] = row["c"]

        rows = db.query(conn, "SELECT occurred_at FROM alerts WHERE occurred_at>=?",
                        (now - 23 * 3600,))
        counts: dict[str, int] = {}
        for row in rows:
            key = datetime.fromtimestamp(row["occurred_at"]).strftime("%m-%d %H")
            counts[key] = counts.get(key, 0) + 1
        trend = []
        for i in range(23, -1, -1):
            dt = datetime.now() - timedelta(hours=i)
            trend.append({"label": dt.strftime("%H:00"),
                          "count": counts.get(dt.strftime("%m-%d %H"), 0)})

        cameras = db.query(
            conn, "SELECT device_id,location,status FROM devices "
                  "WHERE dtype='camera' ORDER BY device_id")
        for cam in cameras:
            cam["alerts_today"] = db.scalar(
                conn, "SELECT COUNT(*) FROM alerts WHERE camera_id=? "
                      "AND occurred_at>=?", (cam["device_id"], today_start))
            last = conn.execute(
                "SELECT MAX(occurred_at) t FROM alerts WHERE camera_id=?",
                (cam["device_id"],)).fetchone()
            cam["last_alert"] = (datetime.fromtimestamp(last["t"]).strftime(
                "%H:%M:%S") if last and last["t"] else "")

        latest = [_serialize_alert(r) for r in db.query(
            conn, "SELECT * FROM alerts ORDER BY occurred_at DESC LIMIT 6")]
        return ok({"stats": stats, "severity": severity, "types": types,
                   "trend": trend, "cameras": cameras, "latest": latest,
                   "system": {"version": cfg["app"]["version"],
                              "runtime": bool(runtime),
                              "uptime_sec": METRICS.snapshot()["uptime_sec"]}})

    # ---------- 预警中心 ----------
    @app.get("/api/v1/alerts")
    def list_alerts(status: str = "", severity: str = "", alert_type: str = "",
                    camera_id: str = "", q: str = "", limit: int = Query(50, le=200),
                    offset: int = 0,
                    user: dict = Depends(require("alert.view")),
                    conn: sqlite3.Connection = Depends(get_conn)):
        where, args = ["1=1"], []
        for value, column in ((status, "status"), (severity, "severity"),
                              (alert_type, "alert_type"),
                              (camera_id, "camera_id")):
            if value:
                where.append(f"{column}=?")
                args.append(value)
        if q:
            where.append("(summary LIKE ? OR location LIKE ?)")
            args += [f"%{q}%", f"%{q}%"]
        clause = " AND ".join(where)
        total = db.scalar(conn, f"SELECT COUNT(*) FROM alerts WHERE {clause}",
                          tuple(args))
        rows = db.query(conn, f"SELECT * FROM alerts WHERE {clause} "
                              "ORDER BY occurred_at DESC LIMIT ? OFFSET ?",
                        (*args, limit, offset))
        return ok({"records": [_serialize_alert(r) for r in rows],
                   "total": total})

    @app.get("/api/v1/alerts/{alert_id}")
    def get_alert(alert_id: str, user: dict = Depends(require("alert.view")),
                  conn: sqlite3.Connection = Depends(get_conn)):
        row = conn.execute("SELECT * FROM alerts WHERE alert_id=?",
                           (alert_id,)).fetchone()
        if not row:
            raise ApiError(40400, "预警不存在")
        data = _serialize_alert(dict(row))
        data["interventions"] = db.query(
            conn, "SELECT * FROM interventions WHERE alert_id=? ORDER BY id",
            (alert_id,))
        data["report_text"] = engine.analyzer.deep_report(data)
        return ok(data)

    @app.get("/api/v1/alerts/{alert_id}/report")
    def alert_report(alert_id: str, user: dict = Depends(require("alert.view")),
                     conn: sqlite3.Connection = Depends(get_conn)):
        row = conn.execute("SELECT * FROM alerts WHERE alert_id=?",
                           (alert_id,)).fetchone()
        if not row:
            raise ApiError(40400, "预警不存在")
        return ok({"alert_id": alert_id,
                   "report_text": engine.analyzer.deep_report(
                       _serialize_alert(dict(row)))})

    @app.post("/api/v1/alerts/{alert_id}/ack")
    def ack_alert(alert_id: str, user: dict = Depends(require("alert.handle")),
                  conn: sqlite3.Connection = Depends(get_conn)):
        changed = db.execute(
            conn, "UPDATE alerts SET status='acknowledged',ack_by=?,ack_at=? "
                  "WHERE alert_id=? AND status='pending'",
            (user["username"], time.time(), alert_id))
        if not changed:
            raise ApiError(40400, "预警不存在或已被受理")
        db.add_intervention(conn, alert_id, "ack", user["username"], "受理预警")
        db.audit(conn, user["username"], "alert.ack", alert_id)
        METRICS.incr("alert.ack")
        return ok({"alert_id": alert_id, "status": "acknowledged"})

    @app.post("/api/v1/alerts/{alert_id}/intervene")
    def intervene(alert_id: str, body: dict[str, Any],
                  user: dict = Depends(require("alert.handle")),
                  conn: sqlite3.Connection = Depends(get_conn)):
        kind = str(body.get("kind", "")).strip()
        content = str(body.get("content", "")).strip()
        if kind not in ("voice", "dispatch", "notify", "onsite"):
            raise ApiError(40000, "kind 限 voice/dispatch/notify/onsite")
        row = conn.execute("SELECT status FROM alerts WHERE alert_id=?",
                           (alert_id,)).fetchone()
        if not row:
            raise ApiError(40400, "预警不存在")
        if row["status"] == "closed":
            raise ApiError(40000, "预警已闭环，无法继续干预")
        db.execute(conn, "UPDATE alerts SET status='intervening' WHERE alert_id=?",
                   (alert_id,))
        db.add_intervention(conn, alert_id, kind, user["username"],
                            content or _default_action(kind))
        db.audit(conn, user["username"], f"alert.intervene.{kind}", alert_id)
        METRICS.incr(f"alert.intervene.{kind}")
        return ok({"alert_id": alert_id, "status": "intervening", "kind": kind})

    @app.post("/api/v1/alerts/{alert_id}/close")
    def close_alert(alert_id: str, body: dict[str, Any],
                    user: dict = Depends(require("alert.handle")),
                    conn: sqlite3.Connection = Depends(get_conn)):
        changed = db.execute(
            conn, "UPDATE alerts SET status='closed',closed_by=?,closed_at=? "
                  "WHERE alert_id=? AND status!='closed'",
            (user["username"], time.time(), alert_id))
        if not changed:
            raise ApiError(40400, "预警不存在或已闭环")
        db.add_intervention(conn, alert_id, "close", user["username"],
                            str(body.get("result", "现场处置完成")))
        db.audit(conn, user["username"], "alert.close", alert_id)
        METRICS.incr("alert.close")
        return ok({"alert_id": alert_id, "status": "closed"})

    # ---------- 视觉检测 ----------
    @app.post("/api/v1/vision/detect")
    def vision_detect(body: dict[str, Any],
                      user: dict = Depends(require("vision.detect")),
                      conn: sqlite3.Connection = Depends(get_conn)):
        frames = body.get("frames")
        if not isinstance(frames, list) or not frames:
            raise ApiError(40000, "frames 不能为空（关键点帧序列）")
        alerts = engine.analyze_vision(
            frames, str(body.get("camera_id", "unknown")),
            str(body.get("location", "未知区域")), bool(body.get("force")))
        persisted = [_persist_alert(conn, alert) for alert in alerts]
        db.audit(conn, user["username"], "vision.detect",
                 f"输入 {len(frames)} 帧，生成 {len(alerts)} 条预警")
        METRICS.incr("vision.detect")
        return ok({"alerts": persisted, "count": len(persisted),
                   "deduped": not alerts})

    # ---------- 场景演示 ----------
    @app.get("/api/v1/scenarios")
    def scenarios(user: dict = Depends(require())):
        return ok({"scenarios": runner.catalog()})

    @app.post("/api/v1/scenarios/{name}")
    def run_scenario(name: str, user: dict = Depends(require("scenario.run")),
                     conn: sqlite3.Connection = Depends(get_conn)):
        try:
            result = runner.run(name)
        except KeyError:
            raise ApiError(40400, f"未知场景：{name}")
        for event in result["events"]:
            db.save_event(conn, event)
            if event.get("event_type") == "work_order":
                db.save_work_order(conn, event)
        for alert in result["alerts"]:
            _persist_alert(conn, alert)
        for violation in result["violations"]:
            db.save_event(conn, violation["event"])
            db.save_violation(conn, violation["event"])
        for push in result["pushes"]:
            db.save_push(conn, push)
        db.audit(conn, user["username"], "scenario.run",
                 f"运行 {name}：预警 {len(result['alerts'])} / 事件 "
                 f"{len(result['events'])} / 工单 {len(result['orders'])}")
        METRICS.incr(f"scenario.{name}")
        return ok(result)

    # ---------- 图书馆占座扩展：事件接入 ----------
    @app.post("/api/seat_event")
    @app.post("/api/v1/seat/events")
    def seat_event(body: dict[str, Any], request: Request,
                   conn: sqlite3.Connection = Depends(get_conn)):
        token = str(cfg["app"].get("integration_token") or "")
        if token and request.headers.get("X-Integration-Token") != token:
            raise ApiError(40300, "集成令牌无效")
        if runtime is None:
            raise ApiError(40000, "campus_ai 运行时不可用，无法接入占座事件")
        result = runtime.handle_seat_event({
            "camera_id": body.get("camera_id", "lib-cam-01"),
            "seat_id": body.get("seat_id"),
            "zone_name": body.get("zone_name"),
            "absent_minutes": body.get("absent_minutes", 0),
            "items": body.get("items") or [],
            "repeat_count": body.get("repeat_count", 1),
            "snapshot": body.get("snapshot", ""),
            "reservation": body.get("reservation"),
            "multi_seat_neighbors": body.get("multi_seat_neighbors") or [],
        })
        event = result["event"]
        db.save_event(conn, event)
        db.save_work_order(conn, event)
        db.audit(conn, "seat_monitor", "seat.event",
                 f"{body.get('seat_id')} / {body.get('absent_minutes')} 分钟")
        METRICS.incr("seat.event")
        return result

    # ---------- 事件 / 工单 / 违规 / 推送 ----------
    @app.get("/api/v1/events")
    def list_events(event_type: str = "", severity: str = "",
                    limit: int = Query(50, le=200), offset: int = 0,
                    user: dict = Depends(require("event.view")),
                    conn: sqlite3.Connection = Depends(get_conn)):
        where, args = ["1=1"], []
        if event_type:
            where.append("event_type=?")
            args.append(event_type)
        if severity:
            where.append("severity=?")
            args.append(severity)
        clause = " AND ".join(where)
        total = db.scalar(conn, f"SELECT COUNT(*) FROM events WHERE {clause}",
                          tuple(args))
        rows = db.query(conn, f"SELECT * FROM events WHERE {clause} "
                              "ORDER BY id DESC LIMIT ? OFFSET ?",
                        (*args, limit, offset))
        for row in rows:
            row["detail"] = {} if user["role"] == "viewer" else json.loads(
                row.get("detail") or "{}")
            row["created_text"] = datetime.fromtimestamp(
                row["created_at"]).strftime("%Y-%m-%d %H:%M:%S")
        return ok({"records": rows, "total": total})

    @app.get("/api/v1/orders")
    def list_orders(status: str = "",
                    user: dict = Depends(require("order.view")),
                    conn: sqlite3.Connection = Depends(get_conn)):
        sql = "SELECT * FROM work_orders"
        args: tuple = ()
        if status:
            sql += " WHERE status=?"
            args = (status,)
        rows = db.query(conn, sql + " ORDER BY id DESC LIMIT 200", args)
        for row in rows:
            row["created_text"] = datetime.fromtimestamp(
                row["created_at"]).strftime("%m-%d %H:%M")
        return ok({"records": rows})

    @app.post("/api/v1/orders/{event_id}/close")
    def close_order(event_id: str, user: dict = Depends(require("order.close")),
                    conn: sqlite3.Connection = Depends(get_conn)):
        changed = db.execute(
            conn, "UPDATE work_orders SET status='closed',closed_at=?,closed_by=? "
                  "WHERE event_id=? AND status='pending'",
            (time.time(), user["username"], event_id))
        if not changed:
            raise ApiError(40400, "工单不存在或已闭环")
        db.audit(conn, user["username"], "order.close", event_id)
        METRICS.incr("order.closed")
        return ok({"event_id": event_id, "status": "closed"})

    @app.get("/api/v1/violations")
    def list_violations(user: dict = Depends(require("violation.view")),
                        conn: sqlite3.Connection = Depends(get_conn)):
        return ok({"records": db.query(
            conn, "SELECT * FROM violations ORDER BY id DESC LIMIT 200")})

    @app.get("/api/v1/pushes")
    def list_pushes(user: dict = Depends(require("push.view")),
                    conn: sqlite3.Connection = Depends(get_conn)):
        rows = db.query(conn, "SELECT * FROM push_records ORDER BY id DESC "
                              "LIMIT 200")
        for row in rows:
            row["targets"] = json.loads(row.get("targets") or "[]")
            row["created_text"] = datetime.fromtimestamp(
                row["created_at"]).strftime("%m-%d %H:%M:%S")
        return ok({"records": rows})

    # ---------- 设备 / 用户 ----------
    @app.get("/api/v1/devices")
    def list_devices(user: dict = Depends(require("device.view")),
                     conn: sqlite3.Connection = Depends(get_conn)):
        return ok({"records": db.query(
            conn, "SELECT * FROM devices ORDER BY dtype,device_id")})

    @app.post("/api/v1/devices")
    def add_device(body: dict[str, Any],
                   user: dict = Depends(require("device.manage")),
                   conn: sqlite3.Connection = Depends(get_conn)):
        device_id = str(body.get("device_id", "")).strip()
        dtype = str(body.get("dtype", "")).strip()
        location = str(body.get("location", "")).strip()
        if not device_id or not location or dtype not in (
                "camera", "intercom", "alarm"):
            raise ApiError(40000, "device_id/location 必填，dtype 限 "
                                  "camera/intercom/alarm")
        try:
            db.execute(conn, "INSERT INTO devices(device_id,dtype,location,status)"
                             " VALUES(?,?,?,'online')", (device_id, dtype, location))
        except sqlite3.IntegrityError:
            raise ApiError(40900, "设备编号已存在")
        db.audit(conn, user["username"], "device.add", device_id)
        return ok({"device_id": device_id})

    @app.get("/api/v1/users")
    def list_users(user: dict = Depends(require("user.manage")),
                   conn: sqlite3.Connection = Depends(get_conn)):
        rows = db.query(conn, "SELECT id,username,name,role,enabled,created_at "
                              "FROM users ORDER BY id")
        for row in rows:
            row["role_label"] = auth.ROLE_LABELS.get(row["role"], "")
        return ok({"records": rows})

    @app.post("/api/v1/users")
    def add_user(body: dict[str, Any],
                 user: dict = Depends(require("user.manage")),
                 conn: sqlite3.Connection = Depends(get_conn)):
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", ""))
        name = str(body.get("name", "")).strip() or username
        role = str(body.get("role", "")).strip()
        if not username or len(password) < 6 or role not in auth.ROLE_PERMISSIONS:
            raise ApiError(40000, "用户名必填、密码至少 6 位、角色非法")
        try:
            db.execute(conn, "INSERT INTO users(username,password_hash,name,role,"
                             "created_at) VALUES(?,?,?,?,?)",
                       (username, auth.hash_password(password), name, role,
                        time.time()))
        except sqlite3.IntegrityError:
            raise ApiError(40900, "用户名已存在")
        db.audit(conn, user["username"], "user.add", username)
        return ok({"username": username})

    # ---------- 监控与审计 ----------
    @app.get("/api/v1/metrics")
    def metrics(user: dict = Depends(require("metrics.view")),
                conn: sqlite3.Connection = Depends(get_conn)):
        snap = METRICS.snapshot()
        snap["db"] = {
            "alerts": db.scalar(conn, "SELECT COUNT(*) FROM alerts"),
            "events": db.scalar(conn, "SELECT COUNT(*) FROM events"),
            "orders_pending": db.scalar(
                conn, "SELECT COUNT(*) FROM work_orders WHERE status='pending'"),
            "violations": db.scalar(conn, "SELECT COUNT(*) FROM violations"),
            "pushes": db.scalar(conn, "SELECT COUNT(*) FROM push_records"),
            "interventions": db.scalar(conn, "SELECT COUNT(*) FROM interventions"),
        }
        return ok(snap)

    @app.get("/api/v1/audit")
    def list_audit(user: dict = Depends(require("audit.view")),
                   conn: sqlite3.Connection = Depends(get_conn)):
        rows = db.query(conn, "SELECT * FROM audit_logs ORDER BY id DESC "
                              "LIMIT 100")
        for row in rows:
            row["created_text"] = datetime.fromtimestamp(
                row["created_at"]).strftime("%m-%d %H:%M:%S")
        return ok({"records": rows})

    @app.get("/api/v1/danger-words")
    def get_danger_words(user: dict = Depends(require("alert.view"))):
        if runtime is not None:
            return ok(runtime.danger_words())
        from ai.audio.danger_words import DangerWordMatcher
        return ok(DangerWordMatcher.DEFAULT)

    @app.post("/api/v1/danger-words")
    def set_danger_words(body: dict[str, list[str]],
                         user: dict = Depends(require("alert.handle"))):
        if runtime is None:
            raise ApiError(40000, "运行时不可用，无法热更新词库")
        return ok(runtime.reload_danger_words(body))

    # ---------- 静态资源 ----------
    audio_dir = resolve_path(cfg["app"].get("audio_dir", "audio_assets"))
    if os.path.isdir(audio_dir):
        app.mount("/audio_assets", StaticFiles(directory=audio_dir),
                  name="audio")
    web_dir = resolve_path(cfg["app"].get("web_dir", "web"))
    if os.path.isdir(web_dir):
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")

    return app


def _default_action(kind: str) -> str:
    return {"voice": "播放语音震慑话术", "dispatch": "调度安保人员到场",
            "notify": "通知班主任与值班领导", "onsite": "值班人员现场处置"}.get(
        kind, "人工干预")


def _persist_alert(conn: sqlite3.Connection, alert: dict[str, Any]) -> dict[str, Any]:
    alert.setdefault("event_id", new_event_id())
    created = db.save_alert(conn, alert)
    if not created:
        return _serialize_alert(dict(alert))
    db.save_event(conn, {
        "event_id": alert["event_id"], "event_type": "alert",
        "severity": alert["severity"], "source": alert.get("camera_id", ""),
        "location": alert.get("location", ""), "summary": alert.get("summary", ""),
        "detail": alert.get("evidence", {}), "timestamp": alert["occurred_at"],
    })
    auto = alert.get("auto_action") or {}
    if auto:
        db.add_intervention(conn, alert["alert_id"], auto.get("kind", "voice"),
                            "system", auto.get("content", ""))
    return _serialize_alert(dict(alert))
