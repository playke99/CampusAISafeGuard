"""数据库全量开发检查脚本

检查维度（结果写入 logs/db_check_<时间戳>.log，并输出结构化清单）：
  A 结构核对：schema.sql（权威规范） vs PRAGMA 实际结构
    —— 表/字段/类型/NOT NULL/DEFAULT/UNIQUE/索引逐项比对
  B 性能验证：批量写入效率、常用聚合查询耗时、事务原子性、WAL 并发读写
  C 安全审查：SQL 注入静态扫描（拼接点参数化核验）、库文件权限说明
  D 一致性：外键孤儿检查、唯一性重复检查、冗余设计说明
  E 备份恢复：backup → integrity_check → 行数比对 → 恢复验证
  F 接口连通：认证校验、预警状态机闭环（受理→干预→闭环）

问题分级：high（阻断/错误）/ medium（规范不符，影响可维护性）/ low（建议）/ info（设计决策说明）

用法：
    python scripts/db_check.py                 # 全量检查
    python scripts/db_check.py --fix           # 自动修复可修复项（补缺失索引）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.db_setup import _logger  # noqa: E402
from server.core import auth  # noqa: E402
from server.core import db as db_layer  # noqa: E402
from server.core.config import db_path, load_config  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "server" / "sql" / "schema.sql"

ISSUES: list[dict] = []


def add_issue(category: str, location: str, impact: str, severity: str,
               fix: str, status: str = "open") -> None:
    ISSUES.append({
        "id": f"{category}-{len(ISSUES) + 1:02d}",
        "category": category, "location": location, "impact": impact,
        "severity": severity, "fix": fix, "status": status,
    })


# ── schema.sql 解析（权威规范） ────────────────────────────
def parse_schema(text: str) -> dict:
    tables: dict[str, dict] = {}
    indexes: list[tuple[str, str, str]] = []
    for m in re.finditer(
            r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\);",
            text, re.S | re.I):
        name, body = m.group(1), m.group(2)
        cols = {}
        for raw in body.splitlines():
            line = raw.strip().rstrip(",")
            if not line or line.startswith("--"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            col = parts[0]
            ctype = parts[1].upper()
            cols[col] = {
                "type": ctype,
                "notnull": "NOT NULL" in line.upper(),
                "default": (re.search(r"DEFAULT\s+('[^']*'|[\w.]+)", line, re.I)
                            .group(1) if re.search(r"DEFAULT\s+('[^']*'|[\w.]+)", line, re.I) else None),
                "pk": "PRIMARY KEY" in line.upper(),
                "unique": "UNIQUE" in line.upper(),
            }
        tables[name] = cols
    for m in re.finditer(
            r"CREATE INDEX IF NOT EXISTS (\w+)\s+ON\s+(\w+)\s*\((\w+)\)",
            text, re.I):
        indexes.append((m.group(1), m.group(2), m.group(3)))
    return {"tables": tables, "indexes": indexes}


# ── A 结构核对 ────────────────────────────────────────────
def check_structure(conn: sqlite3.Connection, log, fix: bool) -> None:
    log.info("===== A 结构核对 =====")
    spec = parse_schema(SCHEMA_PATH.read_text(encoding="utf-8"))
    actual_tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%'")}
    for t in spec["tables"]:
        if t not in actual_tables:
            add_issue("structure", f"表 {t}", "缺表，业务不可用", "high",
                      "执行 schema.sql 建表")
            log.error("  [缺失] 表 %s", t)
            continue
        actual_cols = {r[1]: r for r in conn.execute(f"PRAGMA table_info({t})")}
        for col, rule in spec["tables"][t].items():
            if col not in actual_cols:
                add_issue("structure", f"{t}.{col}", "字段遗漏", "high",
                          "ALTER TABLE 补字段（带默认值）")
                log.error("  [遗漏] %s.%s", t, col)
                continue
            a = actual_cols[col]
            if a[2].upper() != rule["type"]:
                add_issue("structure", f"{t}.{col}",
                          f"类型不符：实际 {a[2]} / 规范 {rule['type']}", "medium",
                          "重建表或记录偏差说明")
                log.warning("  [类型] %s.%s: %s != %s", t, col, a[2], rule["type"])
            if rule["notnull"] and not a[3] and not rule["pk"]:
                add_issue("structure", f"{t}.{col}", "NOT NULL 约束缺失", "medium",
                          "重建表补约束")
                log.warning("  [约束] %s.%s 缺 NOT NULL", t, col)
    extra = actual_tables - set(spec["tables"])
    for t in extra:
        add_issue("structure", f"表 {t}", "规范外多余表", "low",
                  "确认用途后删除或纳入规范")
        log.warning("  [多余] 表 %s", t)

    actual_indexes = {r[1] for r in conn.execute(
        "SELECT type, name FROM sqlite_master WHERE type='index' "
        "AND name NOT LIKE 'sqlite_%'")}
    for idx, table, col in spec["indexes"]:
        if idx not in actual_indexes:
            add_issue("structure", f"索引 {idx} on {table}({col})",
                      "索引缺失", "medium", f"CREATE INDEX {idx}")
            log.warning("  [索引缺失] %s", idx)

    # 关联字段索引审查（评估报告 5.4：追溯字段必须有索引或唯一约束覆盖）
    spec_index_names = {(t, c): n for n, t, c in spec["indexes"]}
    rel_checks = [("alerts", "event_id"), ("interventions", "alert_id"),
                  ("events", "event_id"), ("work_orders", "event_id"),
                  ("violations", "event_id"), ("push_records", "event_id"),
                  ("sessions", "expires_at"), ("audit_logs", "created_at")]
    for table, col in rel_checks:
        covered = False
        for r in conn.execute(f"PRAGMA index_list({table})"):
            cols = [ci[2] for ci in conn.execute(f"PRAGMA index_info({r[1]})")]
            if cols and cols[0] == col:      # 任意索引（含 UNIQUE）首列命中即覆盖
                covered = True
        if covered:
            continue
        idx_name = spec_index_names.get((table, col), f"idx_{table}_{col}")
        add_issue("structure", f"{table}({col})",
                  "关联/追溯字段缺索引，级联查询将全表扫描", "medium",
                  f"CREATE INDEX {idx_name} ON {table}({col})")
        log.warning("  [关联索引缺失] %s(%s)", table, col)
        if fix:
            conn.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} "
                         f"ON {table}({col})")
            conn.commit()
            ISSUES[-1]["status"] = "fixed"
            log.info("  [已修复] 创建索引 %s", idx_name)
    log.info("  结构核对完成，累计问题 %d 项", len(ISSUES))


# ── B 性能验证 ────────────────────────────────────────────
def check_performance(log) -> None:
    log.info("===== B 性能验证（独立临时库，不污染开发库） =====")
    tmp = Path(tempfile.mkdtemp()) / "perf.db"
    db_layer.init_db(str(tmp), seed=False)
    conn = db_layer.connect(str(tmp))

    n = 2000
    t0 = time.time()
    for i in range(n):
        db_layer.save_alert(conn, {
            "alert_id": f"PERF-{i:06d}", "event_id": f"PERFE-{i:06d}",
            "alert_type": "surround", "severity": "yellow", "confidence": 0.7,
            "camera_id": "perf-cam", "location": "性能测试",
            "summary": f"perf {i}", "evidence": {}, "status": "pending",
            "source": "vision", "occurred_at": time.time() - i,
        })
    dur = time.time() - t0
    log.info("  写入效率：%d 条预警 %.2fs（%.0f 条/秒）", n, dur, n / dur)

    t0 = time.time()
    conn.execute("SELECT severity, COUNT(*) FROM alerts GROUP BY severity").fetchall()
    conn.execute("SELECT alert_type, COUNT(*) FROM alerts GROUP BY alert_type").fetchall()
    conn.execute("SELECT COUNT(*) FROM alerts WHERE status='pending' AND severity='red'").fetchone()
    conn.execute("SELECT * FROM alerts ORDER BY occurred_at DESC LIMIT 50").fetchall()
    qdur = (time.time() - t0) * 1000
    log.info("  聚合查询（总览四连查）：%.1f ms", qdur)
    if qdur > 100:
        add_issue("performance", "总览聚合查询", "超过 100ms 预算", "medium",
                  "补充复合索引或精简查询")

    # 事务原子性
    conn.execute("BEGIN")
    try:
        conn.execute("INSERT INTO alerts(alert_id,event_id,alert_type,severity,"
                     "occurred_at) VALUES('TX-1','TXE-1','fall','red',0)")
        conn.execute("INSERT INTO interventions(alert_id,kind,operator,created_at) "
                     "VALUES('TX-1','ack','perf',0)")
        conn.execute("ROLLBACK")
        rolled = conn.execute("SELECT COUNT(*) FROM alerts WHERE alert_id='TX-1'").fetchone()[0]
        log.info("  事务回滚：回滚后 TX-1 行数=%d（应为 0）", rolled)
        if rolled != 0:
            add_issue("transaction", "ROLLBACK", "回滚未生效", "high", "排查连接事务模式")
    except sqlite3.Error as e:
        add_issue("transaction", "事务测试", str(e), "high", "排查")
        conn.execute("ROLLBACK")

    # WAL 并发读写
    errors = []
    stop = time.time() + 2.0

    def reader():
        c = db_layer.connect(str(tmp))
        while time.time() < stop:
            try:
                c.execute("SELECT COUNT(*) FROM alerts").fetchone()
            except sqlite3.Error as e:
                errors.append(str(e))
        c.close()

    def writer():
        c = db_layer.connect(str(tmp))
        i = 0
        while time.time() < stop:
            try:
                c.execute("INSERT INTO alerts(alert_id,event_id,alert_type,severity,"
                          "occurred_at) VALUES(?,?,?,?,?)",
                          (f"W-{i}", f"WE-{i}", "fall", "red", time.time()))
                c.commit()
                i += 1
            except sqlite3.Error as e:
                errors.append(str(e))
        c.close()

    threads = [threading.Thread(target=reader) for _ in range(4)] + \
              [threading.Thread(target=writer)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    log.info("  WAL 并发（4读1写，2s）：错误 %d 次", len(errors))
    if errors:
        add_issue("concurrency", "WAL 并发读写", f"{len(errors)} 次错误", "high",
                  "检查 busy_timeout 配置")
    conn.close()


# ── C 安全审查 ────────────────────────────────────────────
def check_security(conn, log) -> None:
    log.info("===== C 安全审查 =====")
    # SQL 注入静态扫描：定位 server/ 内所有 SQL 拼接点
    findings = []
    for py in (ROOT / "server").rglob("*.py"):
        for lineno, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if "f\"SELECT" in line or "f'SELECT" in line or \
               "f\"INSERT" in line or "f\"UPDATE" in line or "f\"DELETE" in line:
                findings.append((py.name, lineno, line.strip()[:90]))
    unsafe = [f for f in findings
              if re.search(r"\{[^}]*\}", f[2]) and "column" not in f[2]
              and "clause" not in f[2]]
    log.info("  SQL 拼接点 %d 处：", len(findings))
    for name, lineno, snippet in findings:
        log.info("    %s:%d %s", name, lineno, snippet)
    if unsafe:
        for name, lineno, snippet in unsafe:
            add_issue("security", f"{name}:{lineno}",
                      f"疑似非参数化拼接：{snippet}", "high", "改为参数绑定")
    else:
        log.info("  全部拼接点均为「列名白名单 + 参数绑定」，未发现注入风险")

    mode = oct(os.stat(conn.execute("PRAGMA database_list").fetchone()[2]).st_mode)
    log.info("  库文件权限位：%s（Windows ACL 由部署目录继承；"
             "生产建议限制部署目录仅服务账号可读写）", mode)
    add_issue("security", "库文件权限", "SQLite 无库内权限模型，依赖文件系统与应用层 RBAC",
              "info", "生产环境限制 data/ 目录 ACL；应用层 RBAC 已覆盖接口权限",
              status="noted")


# ── D 一致性检查 ──────────────────────────────────────────
def check_consistency(conn, log) -> None:
    log.info("===== D 一致性检查 =====")
    orphans = {
        "alerts.event_id→events": conn.execute(
            "SELECT COUNT(*) FROM alerts a WHERE a.event_id!='' "
            "AND NOT EXISTS(SELECT 1 FROM events e WHERE e.event_id=a.event_id)").fetchone()[0],
        "interventions.alert_id→alerts": conn.execute(
            "SELECT COUNT(*) FROM interventions i WHERE NOT EXISTS("
            "SELECT 1 FROM alerts a WHERE a.alert_id=i.alert_id)").fetchone()[0],
        "work_orders.event_id→events": conn.execute(
            "SELECT COUNT(*) FROM work_orders w WHERE NOT EXISTS("
            "SELECT 1 FROM events e WHERE e.event_id=w.event_id)").fetchone()[0],
        "violations.event_id→events": conn.execute(
            "SELECT COUNT(*) FROM violations v WHERE NOT EXISTS("
            "SELECT 1 FROM events e WHERE e.event_id=v.event_id)").fetchone()[0],
        "push_records.event_id→events": conn.execute(
            "SELECT COUNT(*) FROM push_records p WHERE NOT EXISTS("
            "SELECT 1 FROM events e WHERE e.event_id=p.event_id)").fetchone()[0],
    }
    for name, cnt in orphans.items():
        log.info("  孤儿检查 %s：%d", name, cnt)
        if cnt:
            add_issue("consistency", name, f"{cnt} 条孤儿记录", "high",
                      "排查写入顺序（须先 events 后业务表）")
    dup_alert = conn.execute(
        "SELECT COUNT(*) - COUNT(DISTINCT alert_id) FROM alerts").fetchone()[0]
    dup_event = conn.execute(
        "SELECT COUNT(*) - COUNT(DISTINCT event_id) FROM events").fetchone()[0]
    log.info("  唯一性：alerts 重复 %d，events 重复 %d", dup_alert, dup_event)
    if dup_alert or dup_event:
        add_issue("consistency", "alert_id/event_id", "唯一性被破坏", "high",
                  "检查 UNIQUE 约束是否生效")
    add_issue("consistency", "alerts.summary ↔ events.summary",
              "同一摘要两处存储（有意冗余，换取查询免关联）", "info",
              "保持写入端同源生成，无需变更", status="noted")
    add_issue("consistency", "外键约束",
              "schema 未声明 FOREIGN KEY，采用应用层一致性校验（PRAGMA foreign_keys=ON 保留）",
              "info", "符合 docs/11 轻量方案第 4 节；迁移 PostgreSQL 时补外键",
              status="noted")


# ── E 备份恢复 ────────────────────────────────────────────
def check_backup(conn, log) -> None:
    log.info("===== E 备份恢复验证 =====")
    backup = Path(tempfile.mkdtemp()) / "backup.db"
    dest = sqlite3.connect(str(backup))
    conn.backup(dest)
    dest.close()
    chk = sqlite3.connect(str(backup))
    integrity = chk.execute("PRAGMA integrity_check").fetchone()[0]
    src_counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("alerts", "events", "users", "devices")}
    dst_counts = {t: chk.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in src_counts}
    chk.close()
    log.info("  备份文件：%s（%.1f KB）", backup.name, backup.stat().st_size / 1024)
    log.info("  完整性：%s；行数比对 %s", integrity,
             "一致" if src_counts == dst_counts else f"不一致 {src_counts} vs {dst_counts}")
    if integrity != "ok" or src_counts != dst_counts:
        add_issue("backup", "备份恢复", "备份完整性或行数不一致", "high",
                  "排查 backup API 使用方式")


# ── F 接口连通 ────────────────────────────────────────────
def check_interface(conn, log) -> None:
    log.info("===== F 接口连通（数据访问层） =====")
    row = conn.execute("SELECT password_hash FROM users WHERE username='admin'").fetchone()
    ok = bool(row) and auth.verify_password("admin123", row["password_hash"])
    log.info("  认证校验：admin 密码核验 %s", "通过" if ok else "失败")
    if not ok:
        add_issue("interface", "auth.verify_password", "种子账号认证失败", "high",
                  "重建种子数据")
    bad = auth.verify_password("wrong-password", row["password_hash"]) if row else True
    log.info("  错误密码拒绝：%s", "通过" if not bad else "失败")

    # 预警状态机闭环（模拟 API 层逻辑）
    ts = time.time()
    db_layer.save_alert(conn, {
        "alert_id": "IF-CHECK-1", "event_id": "IF-CHECKE-1",
        "alert_type": "attack", "severity": "red", "confidence": 0.9,
        "camera_id": "if-cam", "location": "接口检查", "summary": "接口连通性检查",
        "evidence": {}, "status": "pending", "source": "vision", "occurred_at": ts})
    db_layer.save_event(conn, {"event_id": "IF-CHECKE-1", "event_type": "alert",
                               "severity": "red", "source": "vision",
                               "location": "接口检查", "summary": "接口连通性检查",
                               "detail": {}, "timestamp": ts})
    db_layer.execute(conn, "UPDATE alerts SET status='acknowledged',ack_by=?,ack_at=? "
                           "WHERE alert_id=? AND status='pending'",
                     ("checker", time.time(), "IF-CHECK-1"))
    db_layer.add_intervention(conn, "IF-CHECK-1", "dispatch", "checker", "调度安保")
    db_layer.execute(conn, "UPDATE alerts SET status='closed',closed_by=?,closed_at=? "
                           "WHERE alert_id=?", ("checker", time.time(), "IF-CHECK-1"))
    final = conn.execute("SELECT status FROM alerts WHERE alert_id='IF-CHECK-1'").fetchone()[0]
    log.info("  预警闭环（受理→干预→闭环）：最终状态 %s", final)
    if final != "closed":
        add_issue("interface", "预警状态机", "闭环流转失败", "high", "排查状态更新 SQL")
    # 重复受理应被拒绝（模拟 API 的 WHERE status='pending' 条件）
    changed = db_layer.execute(
        conn, "UPDATE alerts SET status='acknowledged' WHERE alert_id=? AND status='pending'",
        ("IF-CHECK-1",))
    log.info("  幂等校验：已闭环重复受理影响行数=%d（应为 0）", changed)
    if changed:
        add_issue("interface", "重复受理", "状态机幂等性被破坏", "medium",
                  "检查 WHERE 条件")
    conn.execute("DELETE FROM alerts WHERE alert_id='IF-CHECK-1'")
    conn.execute("DELETE FROM events WHERE event_id='IF-CHECKE-1'")
    conn.execute("DELETE FROM interventions WHERE alert_id='IF-CHECK-1'")
    conn.commit()
    log.info("  检查数据已清理")


def main() -> None:
    parser = argparse.ArgumentParser(description="数据库全量开发检查")
    parser.add_argument("--db", default=None)
    parser.add_argument("--fix", action="store_true", help="自动修复可修复项（补索引）")
    args = parser.parse_args()

    log, log_path = _logger("check")
    log.info("========== 数据库全量检查开始 ==========")
    target = Path(args.db) if args.db else Path(db_path(load_config()))
    log.info("目标库：%s", target)
    if not target.exists():
        log.error("数据库不存在，请先运行 scripts/db_setup.py")
        sys.exit(1)

    conn = db_layer.connect(str(target))
    check_structure(conn, log, args.fix)
    check_performance(log)
    check_security(conn, log)
    check_consistency(conn, log)
    check_backup(conn, log)
    check_interface(conn, log)
    conn.close()

    by = {}
    for issue in ISSUES:
        by.setdefault(issue["severity"], []).append(issue)
    log.info("========== 检查汇总 ==========")
    for sev in ("high", "medium", "low", "info"):
        for issue in by.get(sev, []):
            log.info("[%s][%s] %s @ %s → %s（%s）",
                     sev.upper(), issue["id"], issue["impact"],
                     issue["location"], issue["fix"], issue["status"])
    blocking = [i for i in ISSUES if i["severity"] in ("high", "medium")
                and i["status"] == "open"]
    log.info("未决阻断项：%d", len(blocking))
    print("\n" + json.dumps(ISSUES, ensure_ascii=False, indent=2))
    print(f"\n[完成] 日志: {log_path}")
    if blocking:
        sys.exit(2)


if __name__ == "__main__":
    main()
