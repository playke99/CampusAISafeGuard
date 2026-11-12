"""SQLite 数据访问层：连接、建表、种子数据、统一写入。"""
from __future__ import annotations

import json
import os
import sqlite3
import time

from . import auth

_SCHEMA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "sql", "schema.sql")


def connect(path: str) -> sqlite3.Connection:
    """每次调用返回独立连接；WAL 模式支持多线程读写。"""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(path: str, seed: bool = True) -> None:
    conn = connect(path)
    try:
        with open(_SCHEMA, encoding="utf-8") as f:
            conn.executescript(f.read())
        if seed:
            _seed_users(conn)
            _seed_devices(conn)
        conn.commit()
    finally:
        conn.close()


def _seed_users(conn: sqlite3.Connection) -> None:
    """预置账号：首次部署后请立即修改密码。"""
    seeds = [
        ("admin", "admin123", "系统管理员", "admin"),
        ("leader", "leader123", "安保负责人", "security_leader"),
        ("security", "security123", "安保值班员", "security_staff"),
        ("teacher", "teacher123", "班主任", "head_teacher"),
        ("viewer", "viewer123", "演示访客", "viewer"),
    ]
    for username, pwd, name, role in seeds:
        exists = conn.execute("SELECT 1 FROM users WHERE username=?",
                              (username,)).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO users(username,password_hash,name,role,created_at) "
                "VALUES(?,?,?,?,?)",
                (username, auth.hash_password(pwd), name, role, time.time()))


def _seed_devices(conn: sqlite3.Connection) -> None:
    seeds = [
        ("dorm-cam-01", "camera", "宿舍楼B座1层楼道", "online"),
        ("dorm-cam-02", "camera", "宿舍楼B座2层楼道", "online"),
        ("teach-cam-01", "camera", "教学楼A栋入口", "online"),
        ("teach-cam-02", "camera", "教学楼A栋2层走廊", "online"),
        ("lib-cam-03", "camera", "图书馆3层自习区", "online"),
        ("play-cam-01", "camera", "操场东南角", "online"),
        ("gate-cam-01", "camera", "校门人脸闸机", "online"),
        ("canteen-cam-01", "camera", "食堂二层", "offline"),
        ("intercom-01", "intercom", "教学楼A栋2层楼道", "online"),
        ("intercom-02", "intercom", "宿舍楼B座值班室", "online"),
        ("alarm-01", "alarm", "宿舍楼B座2层", "online"),
    ]
    for d in seeds:
        conn.execute(
            "INSERT OR IGNORE INTO devices(device_id,dtype,location,status) "
            "VALUES(?,?,?,?)", d)


# ---------- 通用 ----------

def query(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row else 0


def execute(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    cur = conn.execute(sql, params)
    conn.commit()
    return cur.rowcount


def audit(conn: sqlite3.Connection, username: str, action: str,
          detail: str = "") -> None:
    conn.execute(
        "INSERT INTO audit_logs(username,action,detail,created_at) VALUES(?,?,?,?)",
        (username, action, detail, time.time()))
    conn.commit()


# ---------- 业务写入 ----------

def save_alert(conn: sqlite3.Connection, alert: dict) -> bool:
    """写入预警；alert_id 已存在时忽略。"""
    cur = conn.execute(
        "INSERT OR IGNORE INTO alerts(alert_id,event_id,alert_type,severity,"
        "confidence,camera_id,location,summary,evidence,status,source,occurred_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (alert["alert_id"], alert.get("event_id", ""), alert["alert_type"],
         alert["severity"], float(alert.get("confidence", 0)),
         alert.get("camera_id", ""), alert.get("location", ""),
         alert.get("summary", ""),
         json.dumps(alert.get("evidence", {}), ensure_ascii=False),
         alert.get("status", "pending"), alert.get("source", "vision"),
         alert.get("occurred_at", time.time())))
    conn.commit()
    return cur.rowcount > 0


def save_event(conn: sqlite3.Connection, e: dict) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO events(event_id,event_type,severity,source,"
        "location,summary,detail,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (e["event_id"], e["event_type"], e["severity"], e.get("source", ""),
         e.get("location", ""), e.get("summary", ""),
         json.dumps(e.get("detail", {}), ensure_ascii=False),
         e.get("timestamp", time.time())))
    conn.commit()


def save_push(conn: sqlite3.Connection, p: dict) -> None:
    conn.execute(
        "INSERT INTO push_records(event_id,severity,targets,summary,created_at) "
        "VALUES(?,?,?,?,?)",
        (p["event_id"], p["severity"],
         json.dumps(p.get("targets", []), ensure_ascii=False),
         p.get("summary", ""), time.time()))
    conn.commit()


def save_work_order(conn: sqlite3.Connection, event: dict) -> None:
    d = event.get("detail", {})
    conn.execute(
        "INSERT OR IGNORE INTO work_orders(event_id,seat_id,location,status,"
        "created_at) VALUES(?,?,?,'pending',?)",
        (event["event_id"], d.get("seat_id", ""), event.get("location", ""),
         time.time()))
    conn.commit()


def save_violation(conn: sqlite3.Connection, event: dict) -> None:
    d = event.get("detail", {})
    stu = d.get("student") or {}
    conn.execute(
        "INSERT OR IGNORE INTO violations(event_id,label,confidence,student_id,"
        "student_name,major,class_name,needs_review,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (event["event_id"], d.get("violation", ""), d.get("confidence", 0),
         stu.get("student_id", ""), stu.get("name", ""), stu.get("major", ""),
         stu.get("class_name", ""), 1 if d.get("needs_review") else 0,
         time.time()))
    conn.commit()


def add_intervention(conn: sqlite3.Connection, alert_id: str, kind: str,
                     operator: str, content: str = "") -> None:
    conn.execute(
        "INSERT INTO interventions(alert_id,kind,operator,content,created_at) "
        "VALUES(?,?,?,?,?)", (alert_id, kind, operator, content, time.time()))
    conn.commit()
