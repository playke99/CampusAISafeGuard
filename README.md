# 数智卫校 · 校园安全 AI 预警与干预系统

> 基于 AI + 大数据的智慧校园防霸凌平台  
> 计算机视觉识别 → 分级预警 → 人工 / 智能干预 → 全流程归档

---

## 1. 系统定位

面向中职 / 高职校园安全治理场景，依托计算机视觉算法对校园监控画面进行实时智能分析，
精准识别**霸凌行为、聚集冲突、人员摔倒**等危险事件，自动触发**红 / 橙 / 黄 / 蓝四级预警**，
并联动**语音震慑、安保调度、班主任通知、现场处置**等干预手段，完整留存事件证据与处置记录，
形成「行为检测 — 实时告警 — 干预处置 — 闭环归档」的完整业务闭环。

系统同时保留**图书馆占座监测**作为扩展场景，验证同一套预警 / 工单 / 审计底座的可扩展性。

## 2. 核心能力

| 能力 | 说明 |
| --- | --- |
| 视觉行为识别 | COCO-17 姿态关键点：摔倒（躯干倾角 > 65°）、攻击（手腕位移 Z-Score > 3）、围堵（间距 < 3.5×肩宽） |
| 多信号升级 | 围堵 + 攻击同时出现自动判定「疑似校园霸凌」并升级红色 |
| 分级预警 | 红 / 橙 / 黄 / 蓝四级，60 秒内同设备同类型去重，红色自动下发语音震慑 |
| 智能干预 | 语音震慑、安保调度、领导通知、现场处置四类干预，全程记录 |
| 多模态研判 | 真实音频转写 + 危险词分级匹配 + 规则化 LLM 深判，生成结构化报告 |
| 闭环归档 | 预警、事件、工单、推送、干预、审计六类数据统一留痕 |
| 权限体系 | PBKDF2 密码哈希 + 会话 Token + RBAC 角色权限点 |
| 扩展场景 | 图书馆占座工单、行为规范违规识别与人脸定位 |

## 3. 快速开始

```bash
# 1. 安装依赖（已安装可跳过）
pip install -r requirements.txt

# 2. 启动服务（默认 8100 端口）
python -m server.main

# 或 Windows 双击
start.bat
```

打开 <http://127.0.0.1:8100>。

### 预置账号

| 账号 | 密码 | 角色 | 权限 |
| --- | --- | --- | --- |
| admin | admin123 | 系统管理员 | 全部权限 |
| leader | leader123 | 安保负责人 | 预警处置、场景、审计、指标 |
| security | security123 | 安保值班员 | 预警处置、场景、设备查看 |
| teacher | teacher123 | 班主任 | 预警查看、事件、违规、推送 |
| viewer | viewer123 | 演示访客 | 仅事件查看 |

> 首次部署后请立即修改预置密码，或通过「用户与权限」创建新账号。

### 推荐演示路径

1. 登录 `admin / admin123`，进入「态势总览」；
2. 打开「智能场景」，依次运行：
   - `vision_attack`：单人暴力攻击 → 红色预警 + 语音震慑；
   - `vision_fall`：人员摔倒 → 橙色预警；
   - `vision_surround`：多人围堵 → 黄色预警；
   - `vision_bullying`：围堵 + 攻击 → 自动升级「霸凌冲突」红色预警；
   - `agent`：真实音频 `red_01.wav` → 转写 + LLM 研判 + 定向推送；
   - `intercom`：危险词命中 → 告警 + 口头制止；
   - `behavior`：违规检出 + 人脸定位 + 未识别转人工；
   - `seat`：图书馆超时占座 → 生成工单。
3. 在「预警中心」打开预警，依次执行**受理 → 记录干预 → 闭环归档**；
4. 在「事件中心 / 工单闭环 / 审计日志 / 运行指标」查看全链路留痕。

## 4. 目录结构

```text
├─ server/                  # 统一后端（FastAPI + SQLite + RBAC）
│  ├─ api.py                # 全部 REST API
│  ├─ main.py               # 启动入口
│  ├─ core/                 # 配置、数据库、认证、指标
│  └─ sql/schema.sql        # 数据库结构
├─ ai/                      # AI 感知与告警引擎
│  ├─ vision/               # 姿态事件判定与窗口分析
│  ├─ audio/                # VAD 与危险词分级匹配
│  ├─ llm/                  # 风险深判与处置建议
│  └─ alert_engine.py       # 分级告警、去重、升级
├─ campus_ai/               # 可复用 AI 业务运行时
│  ├─ agent_service/        # 多模态 → 结构化报告
│  ├─ intercom_service/     # 对讲收音 → 危险词 → 口头制止
│  ├─ behavior_service/     # 违规识别 → 人脸匹配
│  └─ seat_service/         # 占座事件 → 工单
├─ demo/                    # 演示场景与姿态数据构造
├─ web/                     # 统一态势看板（单文件，无外网 CDN）
├─ seat_monitor/            # 图书馆占座监测扩展（真实摄像头 + 预处理 + 背景建模）
│  ├─ web/                  # 监测演示页（视频/区域/告警/历史 四模块）
│  ├─ preprocess.py         # 帧预处理：缩放 / CLAHE / 去噪
│  └─ background.py         # 背景建模：MOG2 / 帧差降级，静态遗留物判定
├─ config/                  # 应用配置与危险词库
├─ data/                    # SQLite 数据库（运行时生成）
├─ audio_assets/            # 真实语音样本与转写
├─ docs/                    # 标准化项目文档
└─ ppt/                     # PPTX 与 HTML 演示稿
```

## 5. 技术栈

- **后端**：FastAPI + Uvicorn + SQLite（WAL），标准库优先，无重型框架依赖；
- **前端**：原生 HTML/CSS/JS，单文件看板，纯 SVG 图表，无外部 CDN；
- **AI**：姿态关键点纯算法判定（零依赖可测试）；YOLO 权重存在时自动启用，否则降级为演示数据；
- **语音**：能量 VAD + 危险词分级匹配；ASR / LLM 支持离线模拟与本地 Ollama 增强；
- **部署**：Windows / Linux 单机一键启动，可平滑迁移 MySQL / PostgreSQL。

## 6. API 概览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/v1/auth/login` | 登录，返回 Token |
| GET | `/api/v1/overview` | 态势总览 |
| GET | `/api/v1/alerts` | 预警列表（级别 / 状态 / 类型 / 关键词） |
| POST | `/api/v1/alerts/{id}/ack` | 受理预警 |
| POST | `/api/v1/alerts/{id}/intervene` | 记录干预 |
| POST | `/api/v1/alerts/{id}/close` | 闭环归档 |
| POST | `/api/v1/vision/detect` | 关键点帧序列 → 视觉预警 |
| GET/POST | `/api/v1/scenarios`、`/api/v1/scenarios/{name}` | 场景列表 / 运行场景 |
| GET | `/api/v1/events` | 事件归档 |
| GET/POST | `/api/v1/orders`、`/api/v1/orders/{id}/close` | 工单查询 / 闭环 |
| GET | `/api/v1/violations` | 违规记录 |
| GET | `/api/v1/devices`、`/api/v1/users` | 设备与用户管理 |
| GET | `/api/v1/audit`、`/api/v1/metrics` | 审计与指标 |
| POST | `/api/seat_event` | 图书馆占座扩展事件接入（集成令牌） |

完整规范见 [docs/01_系统总体方案.md](docs/01_系统总体方案.md) 第六章（API 接口规范），在线文档 <http://127.0.0.1:8100/api/docs>。

## 7. 图书馆占座扩展

`seat_monitor/` 保留为独立监测服务，可接入真实摄像头：

```bash
# 需额外安装 torch / ultralytics / opencv-python（缺失时自动降级，服务不中断）
python -m seat_monitor.main
```

- 监测服务默认运行在 `8200`，演示页 `http://127.0.0.1:8200` 包含
  **实时视频预览 / 区域状态面板 / 嫌疑告警列表 / 历史记录查询** 四个模块；
- 识别链路：帧预处理（缩放 + CLAHE 光照均衡）→ 背景建模（MOG2 / 帧差降级，
  静态遗留物辅助判定）→ YOLO 人/物品检测 → 座位状态机（空位/在座/暂离/计时/疑似）；
- 全局阈值与按座位阈值均可在线热更新（`POST /api/settings`、`POST /api/seat_thresholds`）；
- 检测到超时占座后推送 `POST http://127.0.0.1:8100/api/seat_event`，
  请求头 `X-Integration-Token` 与 `config/app.json` 的 `app.integration_token` 保持一致；
- 推送成功后，新看板「工单闭环」实时出现待处理工单；
- 监测配置为 `seat_monitor/settings.json`，座位标定写入 `seat_monitor/rois.json`，
  预约数据写入 `seat_monitor/reservations.json`；
- 完整实现方案见 [docs/02_占座监测专项方案.md](docs/02_占座监测专项方案.md)。

## 8. 测试

```bash
python -m pytest tests -q
```

测试覆盖：姿态事件判定、危险词匹配、告警引擎、RBAC、API 闭环、场景运行、占座状态机、
权重降级、图书馆监测流水线。

## 9. 文档与演示

- 项目文档：[docs/](docs/)
- 原始需求参考：[docs/reference/](docs/reference/)
- 演示文稿：[ppt/数智卫校_校园安全AI预警与干预系统_演示文稿.pptx](ppt/) 与 [ppt/演示稿.html](ppt/)

## 10. 合规与隐私说明

- 人脸匹配仅用于校内安全事件定位，演示数据均为虚构；
- 真实部署应遵循《个人信息保护法》《未成年人保护法》及学校数据管理制度；
- 建议开启审计日志留存、最小权限分配，并对监控数据设置访问与保留期限。
