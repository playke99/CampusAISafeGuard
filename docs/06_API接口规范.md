# 06 API 接口规范

---

## 1. 通用约定

| 项 | 约定 |
| --- | --- |
| Base URL | `http://127.0.0.1:8100` |
| 数据格式 | `application/json; charset=utf-8` |
| 成功响应 | `{"code": 0, "data": ...}` |
| 失败响应 | `{"code": 40xxx, "error": "错误描述"}` |
| 认证方式 | `Authorization: Bearer <token>` |
| 在线文档 | `GET /api/docs`（Swagger UI） |
| 时间字段 | 数据库存 Unix 时间戳（秒），接口按需返回格式化文本 |

### 1.1 错误码

| code | HTTP | 含义 |
| --- | --- | --- |
| 40000 | 400 | 参数错误 |
| 40100 | 401 | 未登录 / 登录过期 / 账号密码错误 |
| 40300 | 403 | 无权限 / 集成令牌无效 / 登录失败锁定 |
| 40400 | 404 | 资源不存在 |
| 40900 | 409 | 资源冲突（用户名/设备编号已存在） |

### 1.2 权限点

`alert.view`、`alert.handle`、`scenario.run`、`vision.detect`、`event.view`、`order.view`、`order.close`、
`violation.view`、`push.view`、`device.view`、`device.manage`、`user.manage`、`metrics.view`、`audit.view`。

## 2. 健康检查

### GET `/api/v1/health`

无需认证。

```json
{"code":0,"data":{"status":"up","time":1794452377.12,"version":"2.0.0","runtime":true}}
```

## 3. 认证

### POST `/api/v1/auth/login`

请求：

```json
{"username": "admin", "password": "admin123"}
```

响应：

```json
{
  "code": 0,
  "data": {
    "token": "3f2c...",
    "user": {
      "username": "admin",
      "name": "系统管理员",
      "role": "admin",
      "role_label": "系统管理员",
      "permissions": ["alert.view", "alert.handle", "..."]
    }
  }
}
```

说明：连续失败 5 次锁定 5 分钟。

### POST `/api/v1/auth/logout`

请求头携带 Token，响应 `{"code":0,"data":{"message":"已退出"}}`。

### GET `/api/v1/auth/me`

返回当前登录用户信息与权限点。

## 4. 态势总览

### GET `/api/v1/overview`

权限：`event.view`。

响应字段：

| 字段 | 说明 |
| --- | --- |
| `stats` | 预警总数、今日预警、待处置、红色未闭环、干预数、今日事件、待处理工单、在线/总设备、违规数 |
| `severity` | `red/orange/yellow/blue` 数量 |
| `types` | 各类型数量 |
| `trend` | 近 24 小时按小时的预警数量 |
| `cameras` | 摄像头状态、今日预警数、最近预警时间 |
| `latest` | 最近 6 条预警 |
| `system` | 版本、运行时状态、运行时长 |

## 5. 预警中心

### GET `/api/v1/alerts`

权限：`alert.view`。

查询参数：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `status` | string | `pending/acknowledged/intervening/closed` |
| `severity` | string | `red/orange/yellow/blue` |
| `alert_type` | string | `attack/fall/surround/bullying/...` |
| `camera_id` | string | 设备编号 |
| `q` | string | 摘要/位置关键词 |
| `limit` | int | 默认 50，最大 200 |
| `offset` | int | 偏移量 |

响应：

```json
{"code":0,"data":{"records":[...],"total":12}}
```

### GET `/api/v1/alerts/{alert_id}`

返回预警详情，附加 `interventions` 时间线与 `report_text` 研判报告。

### GET `/api/v1/alerts/{alert_id}/report`

仅返回 `{"alert_id": "...", "report_text": "..."}`。

### POST `/api/v1/alerts/{alert_id}/ack`

权限：`alert.handle`。仅 `pending` 可受理。

```json
{"code":0,"data":{"alert_id":"ALM-20261112-abcd1234","status":"acknowledged"}}
```

### POST `/api/v1/alerts/{alert_id}/intervene`

权限：`alert.handle`。

请求：

```json
{"kind": "voice", "content": "已播放语音震慑，现场学生散开"}
```

`kind` 限 `voice/dispatch/notify/onsite`；`content` 为空时使用默认动作描述。

### POST `/api/v1/alerts/{alert_id}/close`

权限：`alert.handle`。

```json
{"result": "安保到场核实，双方已分开，通知班主任跟进"}
```

响应 `{"alert_id":"...","status":"closed"}`；已闭环再次调用返回 `40400`。

## 6. 视觉检测

### POST `/api/v1/vision/detect`

权限：`vision.detect`。

请求：

```json
{
  "camera_id": "dorm-cam-02",
  "location": "宿舍楼B座2层楼道",
  "force": false,
  "frames": [
    [
      [[100,120],[105,118],[99,121],"... 共 17 个关键点 ..."]
    ],
    [
      [[102,121],[107,119],[101,122],"... 共 17 个关键点 ..."]
    ]
  ]
}
```

响应：

```json
{
  "code": 0,
  "data": {
    "alerts": [
      {
        "alert_id": "ALM-20261112-abcd1234",
        "event_id": "EVT-20261112-efgh5678",
        "alert_type": "bullying",
        "severity": "red",
        "confidence": 0.93,
        "camera_id": "dorm-cam-02",
        "location": "宿舍楼B座2层楼道",
        "summary": "宿舍楼B座2层楼道 检测到疑似校园霸凌（人员摔倒(P0)、疑似攻击(P0)、多人围堵(P1)）",
        "evidence": {
          "signals": [],
          "signal_text": "人员摔倒(P0)、疑似攻击(P0)、多人围堵(P1)",
          "metrics": {},
          "window_frames": 8,
          "snapshot": {"persons": []},
          "advice": []
        },
        "status": "pending",
        "source": "vision",
        "auto_action": {"kind": "voice", "status": "queued"}
      }
    ],
    "count": 1,
    "deduped": false
  }
}
```

`deduped=true` 表示命中 60 秒去重窗口，未产生新预警。

## 7. 智能场景

### GET `/api/v1/scenarios`

权限：登录即可。返回场景目录（名称、标题、分组、描述）。

### POST `/api/v1/scenarios/{name}`

权限：`scenario.run`。可选名称：

`vision_attack`、`vision_fall`、`vision_surround`、`vision_bullying`、`agent`、`intercom`、`behavior`、`seat`。

统一响应结构：

```json
{
  "title": "视觉预警 · 霸凌冲突",
  "steps": ["构造围堵+攻击姿态窗口", "多信号叠加升级", "生成红色预警"],
  "alerts": [],
  "events": [],
  "pushes": [],
  "orders": [],
  "violations": [],
  "report_text": "...",
  "audio": "deter_red.wav"
}
```

## 8. 图书馆占座接入

### POST `/api/seat_event`

别名：`POST /api/v1/seat/events`。

认证：请求头 `X-Integration-Token` 必须与 `config/app.json` 的 `app.integration_token` 一致（默认 `campus-local`）。

请求：

```json
{
  "camera_id": "lib-cam-03",
  "seat_id": "L-301",
  "zone_name": "图书馆3层自习区",
  "absent_minutes": 35,
  "items": ["书包", "水杯"],
  "repeat_count": 1
}
```

响应（原始结构，供监测端读取工单）：

```json
{
  "event": {"event_id": "EVT-...", "severity": "yellow", "summary": "..."},
  "orders": [{"order_id": "...", "push_ok": true}],
  "pushes": []
}
```

## 9. 事件 / 工单 / 违规 / 推送

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/v1/events` | `event.view` | 事件列表，支持 `event_type`、`severity`、分页 |
| GET | `/api/v1/orders` | `order.view` | 工单列表，支持 `status` |
| POST | `/api/v1/orders/{event_id}/close` | `order.close` | 工单闭环 |
| GET | `/api/v1/violations` | `violation.view` | 违规记录 |
| GET | `/api/v1/pushes` | `push.view` | 推送记录 |

`viewer` 角色访问事件列表时，`detail` 返回空对象。

## 10. 设备与用户

### GET `/api/v1/devices`

权限：`device.view`。返回设备编号、类型、位置、状态。

### POST `/api/v1/devices`

权限：`device.manage`。

```json
{"device_id": "dorm-cam-03", "dtype": "camera", "location": "宿舍楼B座3层楼道"}
```

`dtype` 限 `camera/intercom/alarm`；重复编号返回 `40900`。

### GET `/api/v1/users`

权限：`user.manage`。返回账号、姓名、角色、启用状态。

### POST `/api/v1/users`

权限：`user.manage`。

```json
{"username": "teacher2", "password": "******", "name": "李老师", "role": "head_teacher"}
```

密码至少 6 位；用户名重复返回 `40900`。

## 11. 监控与审计

### GET `/api/v1/metrics`

权限：`metrics.view`。返回运行时长、接口计数、登录成功/失败、预警动作计数与数据库统计。

### GET `/api/v1/audit`

权限：`audit.view`。返回最近 100 条审计记录。

### GET `/api/v1/danger-words`

权限：`alert.view`。返回当前危险词库。

### POST `/api/v1/danger-words`

权限：`alert.handle`。

```json
{"red": ["救命", "打死"], "orange": ["别打我"], "yellow": ["威胁"]}
```

热更新词库，立即生效并返回最新词库。

## 12. 静态资源

| 路径 | 说明 |
| --- | --- |
| `/` | Web 态势看板（`web/index.html`） |
| `/audio_assets/*` | 语音样本与转写文本 |
| `/api/docs` | Swagger 在线文档 |

## 13. 调用示例

```bash
# 登录并提取 token
curl -s -X POST http://127.0.0.1:8100/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"admin\",\"password\":\"admin123\"}"

# 查询待处置红色预警
curl -s "http://127.0.0.1:8100/api/v1/alerts?status=pending&severity=red" \
  -H "Authorization: Bearer <token>"

# 受理 → 干预 → 闭环
curl -X POST http://127.0.0.1:8100/api/v1/alerts/<alert_id>/ack \
  -H "Authorization: Bearer <token>"
curl -X POST http://127.0.0.1:8100/api/v1/alerts/<alert_id>/intervene \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d "{\"kind\":\"dispatch\",\"content\":\"已调度安保人员到场\"}"
curl -X POST http://127.0.0.1:8100/api/v1/alerts/<alert_id>/close \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d "{\"result\":\"现场处置完成\"}"
```
