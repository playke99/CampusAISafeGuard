-- 数智卫校 · 校园安全 AI 预警与干预系统
-- 数据库：SQLite（WAL，嵌入式零部署）；生产可平移 MySQL/PostgreSQL
-- 时间字段统一为 Unix 时间戳（REAL，秒）

-- ---------- 用户与会话（RBAC） ----------
CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  username      TEXT    NOT NULL UNIQUE,
  password_hash TEXT    NOT NULL,          -- pbkdf2$iterations$salt$hex
  name          TEXT    NOT NULL,
  role          TEXT    NOT NULL,          -- admin/security_leader/security_staff/head_teacher/viewer
  enabled       INTEGER NOT NULL DEFAULT 1,
  created_at    REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  token      TEXT PRIMARY KEY,
  username   TEXT NOT NULL,
  role       TEXT NOT NULL,
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

-- ---------- 预警中心（核心业务表） ----------
CREATE TABLE IF NOT EXISTS alerts (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  alert_id    TEXT    NOT NULL UNIQUE,      -- ALM-YYYYMMDD-xxxxxxxx
  event_id    TEXT    NOT NULL DEFAULT '',  -- 关联事件追溯号
  alert_type  TEXT    NOT NULL,             -- attack/fall/surround/bullying/danger_word/seat/behavior
  severity    TEXT    NOT NULL,             -- red/orange/yellow/blue
  confidence  REAL    NOT NULL DEFAULT 0,
  camera_id   TEXT    NOT NULL DEFAULT '',
  location    TEXT    NOT NULL DEFAULT '',
  summary     TEXT    NOT NULL DEFAULT '',
  evidence    TEXT    NOT NULL DEFAULT '{}',-- JSON：关键点信号、指标、转写等
  status      TEXT    NOT NULL DEFAULT 'pending', -- pending/acknowledged/intervening/closed
  source      TEXT    NOT NULL DEFAULT 'vision',
  occurred_at REAL    NOT NULL,
  ack_by      TEXT,
  ack_at      REAL,
  closed_by   TEXT,
  closed_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status);
CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity);
CREATE INDEX IF NOT EXISTS idx_alerts_time ON alerts(occurred_at);
CREATE INDEX IF NOT EXISTS idx_alerts_event ON alerts(event_id);

-- ---------- 干预记录（人工/智能处置闭环） ----------
CREATE TABLE IF NOT EXISTS interventions (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  alert_id   TEXT    NOT NULL,
  kind       TEXT    NOT NULL,              -- voice/dispatch/notify/onsite/close
  operator   TEXT    NOT NULL,
  content    TEXT    NOT NULL DEFAULT '',
  status     TEXT    NOT NULL DEFAULT 'done',
  created_at REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_interventions_alert ON interventions(alert_id);

-- ---------- 统一事件归档 ----------
CREATE TABLE IF NOT EXISTS events (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id   TEXT    NOT NULL UNIQUE,
  event_type TEXT    NOT NULL,              -- alert/work_order/violation/report/deterrence
  severity   TEXT    NOT NULL,
  source     TEXT    NOT NULL,
  location   TEXT    NOT NULL,
  summary    TEXT    NOT NULL,
  detail     TEXT    NOT NULL DEFAULT '{}',
  created_at REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_severity ON events(severity);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);

-- ---------- 图书馆占座工单（扩展场景） ----------
CREATE TABLE IF NOT EXISTS work_orders (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id   TEXT    NOT NULL UNIQUE,
  seat_id    TEXT    NOT NULL,
  location   TEXT    NOT NULL,
  status     TEXT    NOT NULL DEFAULT 'pending',
  created_at REAL    NOT NULL,
  closed_at  REAL,
  closed_by  TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_status ON work_orders(status);

-- ---------- 行为规范违规（扩展场景） ----------
CREATE TABLE IF NOT EXISTS violations (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id     TEXT    NOT NULL UNIQUE,
  label        TEXT    NOT NULL,
  confidence   REAL    NOT NULL,
  student_id   TEXT    NOT NULL DEFAULT '',
  student_name TEXT    NOT NULL DEFAULT '',
  major        TEXT    NOT NULL DEFAULT '',
  class_name   TEXT    NOT NULL DEFAULT '',
  needs_review INTEGER NOT NULL DEFAULT 0,
  created_at   REAL    NOT NULL
);

-- ---------- 定向推送记录 ----------
CREATE TABLE IF NOT EXISTS push_records (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id   TEXT    NOT NULL,
  severity   TEXT    NOT NULL,
  targets    TEXT    NOT NULL,
  summary    TEXT    NOT NULL,
  created_at REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_push_event ON push_records(event_id);

-- ---------- 设备台账 ----------
CREATE TABLE IF NOT EXISTS devices (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id TEXT    NOT NULL UNIQUE,
  dtype     TEXT    NOT NULL,              -- camera/intercom/alarm
  location  TEXT    NOT NULL,
  status    TEXT    NOT NULL DEFAULT 'online'
);

-- ---------- 审计日志 ----------
CREATE TABLE IF NOT EXISTS audit_logs (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  username   TEXT    NOT NULL,
  action     TEXT    NOT NULL,
  detail     TEXT    NOT NULL DEFAULT '',
  created_at REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at);
