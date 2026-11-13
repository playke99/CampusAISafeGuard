-- ============================================================================
-- 数智卫校 · 校园安全 AI 预警与干预系统 · 数据库物理设计 v2.0（正式版）
-- 数据库类型：SQLite 3（WAL 模式，嵌入式零部署）
-- 设计规范：第三范式（3NF）、全业务表审计字段、外键约束、受控索引集
-- 时间口径：Unix 时间戳（REAL，秒）；逻辑删除：deleted=1
-- 注意：SQLite 不支持 COMMENT 语法，表/字段注释以行内注释形式标注；
--       外键需连接时执行 PRAGMA foreign_keys = ON（server/core/db.py 已内置）
-- 执行顺序即依赖顺序：users → sessions → devices → events → alerts
--                  → interventions → work_orders → violations → push_records → audit_logs
-- ============================================================================

-- ---------- 1. 用户表（RBAC 账号） ----------
CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,   -- 自增主键
  username      TEXT    NOT NULL UNIQUE,             -- 登录名，业务唯一
  password_hash TEXT    NOT NULL,                    -- pbkdf2$iterations$salt$hex
  name          TEXT    NOT NULL,                    -- 姓名
  role          TEXT    NOT NULL DEFAULT 'viewer',   -- admin/security_leader/security_staff/head_teacher/viewer
  enabled       INTEGER NOT NULL DEFAULT 1,          -- 启用标识：1 启用 / 0 停用
  created_at    REAL    NOT NULL,                    -- 审计：创建时间
  updated_at    REAL,                                -- 审计：更新时间（触发器维护）
  created_by    TEXT    NOT NULL DEFAULT 'system',   -- 审计：创建人
  updated_by    TEXT    NOT NULL DEFAULT 'system',   -- 审计：更新人
  deleted       INTEGER NOT NULL DEFAULT 0           -- 审计：逻辑删除标识
);

-- ---------- 2. 会话表（登录态，临时数据，不挂审计字段） ----------
CREATE TABLE IF NOT EXISTS sessions (
  token      TEXT PRIMARY KEY,                       -- 会话令牌，主键
  username   TEXT NOT NULL REFERENCES users(username),  -- 外键 → users.username
  role       TEXT NOT NULL,                          -- 登录时角色快照
  created_at REAL NOT NULL,                          -- 签发时间
  expires_at REAL NOT NULL                           -- 过期时间
);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);  -- 过期会话清理

-- ---------- 3. 设备台账表 ----------
CREATE TABLE IF NOT EXISTS devices (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,      -- 自增主键
  device_id  TEXT    NOT NULL UNIQUE,                -- 设备编号，业务唯一
  dtype      TEXT    NOT NULL,                       -- 设备类型：camera/intercom/alarm
  location   TEXT    NOT NULL,                       -- 安装位置
  status     TEXT    NOT NULL DEFAULT 'online',      -- online/offline
  created_at REAL    NOT NULL,
  updated_at REAL,
  created_by TEXT    NOT NULL DEFAULT 'system',
  updated_by TEXT    NOT NULL DEFAULT 'system',
  deleted    INTEGER NOT NULL DEFAULT 0
);

-- ---------- 4. 统一事件表（一事一档追溯锚点，先于预警写入） ----------
CREATE TABLE IF NOT EXISTS events (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,      -- 自增主键
  event_id   TEXT    NOT NULL UNIQUE,                -- 事件编号 EVT-YYYYMMDD-xxxxxxxx，业务唯一
  event_type TEXT    NOT NULL,                       -- alert/work_order/violation/report/deterrence
  severity   TEXT    NOT NULL,                       -- red/orange/yellow/blue
  source     TEXT    NOT NULL DEFAULT '',            -- 来源设备/模块
  location   TEXT    NOT NULL DEFAULT '',            -- 发生地点
  summary    TEXT    NOT NULL DEFAULT '',            -- 事件摘要
  detail     TEXT    NOT NULL DEFAULT '{}',          -- JSON 明细（转写/检测结果等）
  created_at REAL    NOT NULL,
  updated_at REAL,
  created_by TEXT    NOT NULL DEFAULT 'system',
  updated_by TEXT    NOT NULL DEFAULT 'system',
  deleted    INTEGER NOT NULL DEFAULT 0
);
-- 事件中心按类型+时间筛选（高频）
CREATE INDEX IF NOT EXISTS idx_events_type_created ON events(event_type, created_at);
-- 态势总览级别分布（中频）
CREATE INDEX IF NOT EXISTS idx_events_severity ON events(severity);
-- 24 小时趋势按时间聚合（高频）
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);

-- ---------- 5. 预警表（核心业务表） ----------
CREATE TABLE IF NOT EXISTS alerts (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,     -- 自增主键
  alert_id    TEXT    NOT NULL UNIQUE,               -- 预警编号 ALM-YYYYMMDD-xxxxxxxx，业务唯一
  event_id    TEXT    NOT NULL REFERENCES events(event_id),  -- 外键 → 统一事件
  alert_type  TEXT    NOT NULL,                      -- attack/fall/surround/bullying/danger_word/seat/behavior
  severity    TEXT    NOT NULL,                      -- red/orange/yellow/blue
  confidence  REAL    NOT NULL DEFAULT 0,            -- 置信度 0~1
  camera_id   TEXT    NOT NULL DEFAULT '',           -- 逻辑外键 → devices.device_id（不强制，见设计说明 4.3）
  location    TEXT    NOT NULL DEFAULT '',
  summary     TEXT    NOT NULL DEFAULT '',
  evidence    TEXT    NOT NULL DEFAULT '{}',         -- JSON：姿态信号/危险词/指标
  status      TEXT    NOT NULL DEFAULT 'pending',    -- pending/acknowledged/intervening/closed
  source      TEXT    NOT NULL DEFAULT 'vision',     -- vision/audio/agent/seat/behavior
  occurred_at REAL    NOT NULL,                      -- 事件发生时间
  ack_by      TEXT,                                  -- 受理人
  ack_at      REAL,                                  -- 受理时间
  closed_by   TEXT,                                  -- 闭环人
  closed_at   REAL,                                  -- 闭环时间
  created_at  REAL    NOT NULL,
  updated_at  REAL,
  created_by  TEXT    NOT NULL DEFAULT 'system',
  updated_by  TEXT    NOT NULL DEFAULT 'system',
  deleted     INTEGER NOT NULL DEFAULT 0
);
-- 预警中心默认视图：按状态过滤 + 时间倒序（最高频查询）
CREATE INDEX IF NOT EXISTS idx_alerts_status_time ON alerts(status, occurred_at);
-- 级别筛选与红色未闭环统计（高频）
CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity);
-- 类型分布统计（中频）
CREATE INDEX IF NOT EXISTS idx_alerts_type ON alerts(alert_type);
-- 外键回查事件（高频，FK 配套）
CREATE INDEX IF NOT EXISTS idx_alerts_event ON alerts(event_id);
-- 设备维度今日预警（摄像头卡片，中频）
CREATE INDEX IF NOT EXISTS idx_alerts_camera ON alerts(camera_id);

-- ---------- 6. 干预记录表（预警处置时间线，追加式） ----------
CREATE TABLE IF NOT EXISTS interventions (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,      -- 自增主键
  alert_id   TEXT    NOT NULL REFERENCES alerts(alert_id),   -- 外键 → 预警
  kind       TEXT    NOT NULL,                       -- voice/dispatch/notify/onsite/close
  operator   TEXT    NOT NULL,                       -- 操作人（即创建人，追加式记录不挂通用审计字段）
  content    TEXT    NOT NULL DEFAULT '',            -- 处置内容
  status     TEXT    NOT NULL DEFAULT 'done',        -- done/failed
  created_at REAL    NOT NULL
);
-- 按预警查询处置时间线（高频，FK 配套）
CREATE INDEX IF NOT EXISTS idx_interventions_alert ON interventions(alert_id);

-- ---------- 7. 占座工单表（图书馆扩展场景，与事件 1:1） ----------
CREATE TABLE IF NOT EXISTS work_orders (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,      -- 自增主键
  event_id   TEXT    NOT NULL UNIQUE REFERENCES events(event_id),  -- 外键 + 唯一（1:1）
  seat_id    TEXT    NOT NULL,                       -- 座位号
  location   TEXT    NOT NULL,                       -- 区域
  status     TEXT    NOT NULL DEFAULT 'pending',     -- pending/closed
  created_at REAL    NOT NULL,
  updated_at REAL,
  created_by TEXT    NOT NULL DEFAULT 'system',
  updated_by TEXT    NOT NULL DEFAULT 'system',
  deleted    INTEGER NOT NULL DEFAULT 0,
  closed_at  REAL,                                   -- 闭环时间
  closed_by  TEXT                                    -- 闭环人
);
-- 工单闭环页：待处理置顶 + 时间倒序（高频）
CREATE INDEX IF NOT EXISTS idx_orders_status_time ON work_orders(status, created_at);

-- ---------- 8. 行为规范违规表（与事件 1:1） ----------
CREATE TABLE IF NOT EXISTS violations (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,    -- 自增主键
  event_id     TEXT    NOT NULL UNIQUE REFERENCES events(event_id),  -- 外键 + 唯一（1:1）
  label        TEXT    NOT NULL,                     -- 违规类型：slippers/breakfast
  confidence   REAL    NOT NULL DEFAULT 0,           -- 检测置信度
  student_id   TEXT    NOT NULL DEFAULT '',          -- 学号（人脸匹配结果，空=待确认）
  student_name TEXT    NOT NULL DEFAULT '',          -- 学生姓名快照（来源外部人脸系统）
  major        TEXT    NOT NULL DEFAULT '',          -- 专业快照
  class_name   TEXT    NOT NULL DEFAULT '',          -- 班级快照
  needs_review INTEGER NOT NULL DEFAULT 0,           -- 待人工复核：1 是 / 0 否
  created_at   REAL    NOT NULL,
  updated_at   REAL,
  created_by   TEXT    NOT NULL DEFAULT 'system',
  updated_by   TEXT    NOT NULL DEFAULT 'system',
  deleted      INTEGER NOT NULL DEFAULT 0
);
-- 按学号追溯个人违规记录（中频）
CREATE INDEX IF NOT EXISTS idx_violations_student ON violations(student_id);
-- 复核队列：待复核 + 时间（中频）
CREATE INDEX IF NOT EXISTS idx_violations_review ON violations(needs_review, created_at);

-- ---------- 9. 定向推送记录表 ----------
CREATE TABLE IF NOT EXISTS push_records (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,      -- 自增主键
  event_id   TEXT    NOT NULL REFERENCES events(event_id),   -- 外键 → 统一事件
  severity   TEXT    NOT NULL,                       -- 推送级别快照
  targets    TEXT    NOT NULL,                       -- JSON 数组：目标角色
  summary    TEXT    NOT NULL DEFAULT '',            -- 推送内容摘要
  created_at REAL    NOT NULL,
  updated_at REAL,
  created_by TEXT    NOT NULL DEFAULT 'system',
  updated_by TEXT    NOT NULL DEFAULT 'system',
  deleted    INTEGER NOT NULL DEFAULT 0
);
-- 按事件回查推送记录（高频，FK 配套）
CREATE INDEX IF NOT EXISTS idx_push_event ON push_records(event_id);

-- ---------- 10. 审计日志表（追加式，不挂通用审计字段） ----------
CREATE TABLE IF NOT EXISTS audit_logs (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,      -- 自增主键
  username   TEXT    NOT NULL,                       -- 操作人（逻辑关联 users.username，不强制 FK 以保留离职账号日志）
  action     TEXT    NOT NULL,                       -- 动作：login/ack/intervene/close/scenario.run/...
  detail     TEXT    NOT NULL DEFAULT '',            -- 操作明细（含 alert_id/event_id）
  created_at REAL    NOT NULL
);
-- 审计页按时间倒序（高频）
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at);
-- 按操作人追溯（中频）
CREATE INDEX IF NOT EXISTS idx_audit_user_time ON audit_logs(username, created_at);

-- ============================================================================
-- 审计字段维护触发器：业务表 UPDATE 时自动刷新 updated_at
-- （created_by/updated_by 由应用层写入；deleted 仅允许 0→1 逻辑删除）
-- ============================================================================
CREATE TRIGGER IF NOT EXISTS trg_users_touch AFTER UPDATE ON users
BEGIN UPDATE users SET updated_at = strftime('%s','now') WHERE id = NEW.id; END;

CREATE TRIGGER IF NOT EXISTS trg_devices_touch AFTER UPDATE ON devices
BEGIN UPDATE devices SET updated_at = strftime('%s','now') WHERE id = NEW.id; END;

CREATE TRIGGER IF NOT EXISTS trg_events_touch AFTER UPDATE ON events
BEGIN UPDATE events SET updated_at = strftime('%s','now') WHERE id = NEW.id; END;

CREATE TRIGGER IF NOT EXISTS trg_alerts_touch AFTER UPDATE ON alerts
BEGIN UPDATE alerts SET updated_at = strftime('%s','now') WHERE id = NEW.id; END;

CREATE TRIGGER IF NOT EXISTS trg_orders_touch AFTER UPDATE ON work_orders
BEGIN UPDATE work_orders SET updated_at = strftime('%s','now') WHERE id = NEW.id; END;

CREATE TRIGGER IF NOT EXISTS trg_violations_touch AFTER UPDATE ON violations
BEGIN UPDATE violations SET updated_at = strftime('%s','now') WHERE id = NEW.id; END;

CREATE TRIGGER IF NOT EXISTS trg_push_touch AFTER UPDATE ON push_records
BEGIN UPDATE push_records SET updated_at = strftime('%s','now') WHERE id = NEW.id; END;
