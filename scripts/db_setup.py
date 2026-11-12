"""数据库全流程搭建脚本（含执行日志）

步骤（每步均写入 logs/db_setup_<时间戳>.log）：
  1. 实例初始化：清理旧库（--reset）→ 建立连接并验证 WAL
  2. 库名配置确认：解析 config/app.json 的 app.db_path
  3. 表结构创建：执行 server/sql/schema.sql（10 表 + 索引）
  4. 字段/约束/索引核验：统计 sqlite_master 实际产物
  5. 外键策略确认：PRAGMA foreign_keys 状态与设计说明
  6. 初始种子数据：5 个预置账号 + 11 台设备（init_db 内建）
  7. 演示数据导入（--demo-data）：预警/事件/干预/工单/违规/推送
  8. 完整性校验：PRAGMA integrity_check

用法：
    python scripts/db_setup.py                 # 按 config 搭建（保留已有库）
    python scripts/db_setup.py --reset         # 重置后重建
    python scripts/db_setup.py --reset --demo-data
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.core import db as db_layer  # noqa: E402
from server.core.config import db_path, load_config  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"


def _logger(tag: str) -> tuple[logging.Logger, Path]:
    LOG_DIR.mkdir(exist_ok=True)
    path = LOG_DIR / f"db_{tag}_{time.strftime('%Y%m%d_%H%M%S')}.log"
    logger = logging.getLogger(f"db_{tag}")
    logger.setLevel(logging.INFO)
    logger.handlers = []
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setFormatter(fmt)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger, path


def _demo_data(conn: sqlite3.Connection, log: logging.Logger) -> None:
    """导入演示数据：覆盖红/橙/黄预警闭环、工单、违规、推送。"""
    now = time.time()
    demo_alerts = [
        {
            "alert_id": "ALM-DEMO-0001", "event_id": "EVT-DEMO-0001",
            "alert_type": "bullying", "severity": "red", "confidence": 0.93,
            "camera_id": "dorm-cam-02", "location": "宿舍楼B座2层楼道",
            "summary": "宿舍楼B座2层楼道 检测到疑似校园霸凌（多人围堵(P1)、疑似攻击(P0)）",
            "evidence": {"signal_text": "多人围堵(P1)、疑似攻击(P0)"},
            "status": "pending", "source": "vision", "occurred_at": now - 300,
        },
        {
            "alert_id": "ALM-DEMO-0002", "event_id": "EVT-DEMO-0002",
            "alert_type": "fall", "severity": "orange", "confidence": 0.86,
            "camera_id": "teach-cam-02", "location": "教学楼A栋2层走廊",
            "summary": "教学楼A栋2层走廊 检测到人员摔倒（躯干倾角 72.5°）",
            "evidence": {"signal_text": "人员摔倒(P0)"},
            "status": "acknowledged", "source": "vision", "occurred_at": now - 1800,
        },
        {
            "alert_id": "ALM-DEMO-0003", "event_id": "EVT-DEMO-0003",
            "alert_type": "surround", "severity": "yellow", "confidence": 0.78,
            "camera_id": "play-cam-01", "location": "操场东南角",
            "summary": "操场东南角 检测到多人围堵（间距 1.2×肩宽）",
            "evidence": {"signal_text": "多人围堵(P0)"},
            "status": "closed", "source": "vision", "occurred_at": now - 7200,
        },
    ]
    for alert in demo_alerts:
        if db_layer.save_alert(conn, alert):
            log.info("  导入预警 %s（%s）", alert["alert_id"], alert["severity"])
        db_layer.save_event(conn, {
            "event_id": alert["event_id"], "event_type": "alert",
            "severity": alert["severity"], "source": alert["source"],
            "location": alert["location"], "summary": alert["summary"],
            "detail": {"alert_id": alert["alert_id"]},
            "timestamp": alert["occurred_at"],
        })
    db_layer.add_intervention(conn, "ALM-DEMO-0002", "ack", "security", "受理预警")
    db_layer.add_intervention(conn, "ALM-DEMO-0002", "dispatch", "security", "调度安保人员到场")
    db_layer.add_intervention(conn, "ALM-DEMO-0003", "close", "leader", "现场确认无冲突，已归档")
    log.info("  导入干预记录 3 条")

    order_event = {
        "event_id": "EVT-DEMO-0004", "event_type": "work_order",
        "severity": "yellow", "source": "seat_monitor",
        "location": "图书馆3层自习区", "summary": "座位 L-301 超时占座 35 分钟",
        "detail": {"seat_id": "L-301", "absent_minutes": 35},
        "timestamp": now - 600,
    }
    db_layer.save_event(conn, order_event)
    db_layer.save_work_order(conn, order_event)
    log.info("  导入占座工单 1 条")

    violation_event = {
        "event_id": "EVT-DEMO-0005", "event_type": "violation",
        "severity": "blue", "source": "behavior",
        "location": "教学楼A栋入口", "summary": "行为规范违规：携带早餐进入",
        "detail": {"violation": "breakfast", "confidence": 0.87,
                   "student": {"student_id": "2023002", "name": "李四",
                               "major": "大数据技术", "class_name": "大数据2302"}},
        "timestamp": now - 3600,
    }
    db_layer.save_event(conn, violation_event)
    db_layer.save_violation(conn, violation_event)
    log.info("  导入违规记录 1 条")

    db_layer.save_push(conn, {
        "event_id": "EVT-DEMO-0001", "severity": "red",
        "targets": ["security_leader", "duty_principal", "head_teacher"],
        "summary": "宿舍楼B座2层楼道 疑似校园霸凌红色预警",
    })
    log.info("  导入推送记录 1 条")


def main() -> None:
    parser = argparse.ArgumentParser(description="数据库全流程搭建")
    parser.add_argument("--reset", action="store_true", help="删除旧库后重建")
    parser.add_argument("--demo-data", action="store_true", help="导入演示数据")
    parser.add_argument("--db", default=None, help="覆盖数据库路径")
    args = parser.parse_args()

    log, log_path = _logger("setup")
    log.info("========== 数据库搭建开始 ==========")
    log.info("执行日志: %s", log_path)

    cfg = load_config()
    target = Path(args.db) if args.db else Path(db_path(cfg))

    # ── STEP 1 实例初始化 ──
    log.info("STEP 1 实例初始化：目标库 %s", target)
    if args.reset and target.exists():
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(target) + suffix)
            if p.exists():
                p.unlink()
                log.info("  已删除旧库文件: %s", p.name)
    target.parent.mkdir(parents=True, exist_ok=True)

    # ── STEP 2 库名配置确认 ──
    log.info("STEP 2 库名配置：app.db_path=%s（来源 config/app.json）",
             cfg["app"]["db_path"])

    # ── STEP 3 表结构创建 ──
    schema = ROOT / "server" / "sql" / "schema.sql"
    log.info("STEP 3 表结构创建：执行 %s", schema)
    db_layer.init_db(str(target), seed=True)
    log.info("  schema.sql 执行完成（含种子数据）")

    # ── STEP 4 结构产物核验 ──
    conn = db_layer.connect(str(target))
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    indexes = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    log.info("STEP 4 结构核验：表 %d 张 %s", len(tables), tables)
    log.info("  索引 %d 个 %s", len(indexes), indexes)

    # ── STEP 5 外键策略 ──
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    log.info("STEP 5 外键策略：PRAGMA foreign_keys=%s；设计采用应用层一致性校验"
             "（轻量方案，见 docs/11 第 4 节）", fk)

    # ── STEP 6 种子数据核验 ──
    users = db_layer.scalar(conn, "SELECT COUNT(*) FROM users")
    devices = db_layer.scalar(conn, "SELECT COUNT(*) FROM devices")
    log.info("STEP 6 种子数据：用户 %d 个，设备 %d 台", users, devices)

    # ── STEP 7 演示数据 ──
    if args.demo_data:
        log.info("STEP 7 演示数据导入：")
        _demo_data(conn, log)
    else:
        log.info("STEP 7 跳过演示数据（--demo-data 启用）")

    # ── STEP 8 完整性校验 ──
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
    log.info("STEP 8 完整性：integrity_check=%s，journal_mode=%s",
             integrity, journal)
    conn.close()

    if integrity != "ok":
        log.error("数据库完整性校验失败！")
        sys.exit(1)
    log.info("========== 数据库搭建完成 ==========")
    print(f"\n[完成] 日志: {log_path}")


if __name__ == "__main__":
    main()
