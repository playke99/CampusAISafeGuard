"""认证与权限：PBKDF2 密码哈希、会话 token、角色权限点（RBAC）。

权限点：
  alert.view      查看预警
  alert.handle    受理 / 干预 / 关闭预警
  scenario.run    运行演示场景
  vision.detect   调用视觉检测接口
  event.view      查看事件
  order.view      查看工单
  order.close     工单闭环
  violation.view  查看违规
  push.view       查看推送
  device.view     查看设备
  device.manage   设备管理
  user.manage     用户管理
  metrics.view    指标查看
  audit.view      审计查看
"""
from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
import time
import uuid

_ITERATIONS = 100_000


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return f"pbkdf2${_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt_hex, hash_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


def create_session(conn: sqlite3.Connection, username: str, role: str,
                   ttl: int) -> str:
    token = uuid.uuid4().hex
    now = time.time()
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
    conn.execute(
        "INSERT INTO sessions(token,username,role,created_at,expires_at) "
        "VALUES(?,?,?,?,?)", (token, username, role, now, now + ttl))
    conn.commit()
    return token


def resolve_session(conn: sqlite3.Connection, token: str) -> dict | None:
    if not token:
        return None
    row = conn.execute(
        "SELECT username,role,expires_at FROM sessions WHERE token=?",
        (token,)).fetchone()
    if not row or row["expires_at"] < time.time():
        return None
    return {"username": row["username"], "role": row["role"]}


def drop_session(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token=?", (token,))
    conn.commit()


ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": {"alert.view", "alert.handle", "scenario.run", "vision.detect",
              "event.view", "order.view", "order.close", "violation.view",
              "push.view", "device.view", "device.manage", "user.manage",
              "metrics.view", "audit.view"},
    "security_leader": {"alert.view", "alert.handle", "scenario.run",
                        "vision.detect", "event.view", "order.view",
                        "order.close", "violation.view", "push.view",
                        "device.view", "metrics.view", "audit.view"},
    "security_staff": {"alert.view", "alert.handle", "scenario.run",
                       "vision.detect", "event.view", "order.view",
                       "order.close", "violation.view", "push.view",
                       "device.view", "metrics.view"},
    "head_teacher": {"alert.view", "event.view", "violation.view", "push.view"},
    "viewer": {"event.view"},
}

ROLE_LABELS = {
    "admin": "系统管理员",
    "security_leader": "安保负责人",
    "security_staff": "安保值班员",
    "head_teacher": "班主任",
    "viewer": "演示访客",
}


def has_permission(role: str, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, set())


def permissions_of(role: str) -> list[str]:
    return sorted(ROLE_PERMISSIONS.get(role, set()))
