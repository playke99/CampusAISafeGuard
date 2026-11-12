"""生成《数智卫校 · 校园安全 AI 预警与干预系统》演示文稿（PPTX）。

用法：
    python ppt/build_ppt.py

输出：
    ppt/数智卫校_校园安全AI预警与干预系统_演示文稿.pptx
"""
from __future__ import annotations

import os

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

# ---------- 主题 ----------
BG = RGBColor(0x0A, 0x18, 0x2E)
BG_CARD = RGBColor(0x11, 0x27, 0x47)
BG_CARD_2 = RGBColor(0x0E, 0x20, 0x3B)
LINE = RGBColor(0x1E, 0x3C, 0x66)
TEXT = RGBColor(0xE8, 0xF2, 0xFF)
MUTED = RGBColor(0x93, 0xAC, 0xCB)
ACCENT = RGBColor(0x35, 0xD0, 0xFF)
ACCENT_2 = RGBColor(0x2F, 0x7C, 0xFF)
RED = RGBColor(0xFF, 0x4D, 0x5E)
ORANGE = RGBColor(0xFF, 0x9F, 0x43)
YELLOW = RGBColor(0xFF, 0xD1, 0x66)
BLUE = RGBColor(0x4D, 0xA3, 0xFF)

FONT = "微软雅黑"
SW, SH = 13.333, 7.5


def _set_ea(run, font: str = FONT) -> None:
    rpr = run._r.get_or_add_rPr()
    ea = rpr.find(qn("a:ea"))
    if ea is None:
        ea = rpr.makeelement(qn("a:ea"), {})
        rpr.append(ea)
    ea.set("typeface", font)


def tx(slide, x, y, w, h, text="", size=14, bold=False, color=TEXT,
       align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, spacing=6, line=1.25,
       font=FONT, italic=False):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = 0
    tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    lines = text.split("\n") if isinstance(text, str) else list(text)
    for i, line_text in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.space_after = Pt(spacing)
        p.line_spacing = line
        run = p.add_run()
        run.text = line_text
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.italic = italic
        run.font.name = font
        run.font.color.rgb = color
        _set_ea(run, font)
    return box


def rect(slide, x, y, w, h, fill=BG_CARD, line_color=LINE, radius=True,
         line_width=0.75):
    shape_type = MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE
    shape = slide.shapes.add_shape(shape_type, Inches(x), Inches(y),
                                   Inches(w), Inches(h))
    if radius:
        shape.adjustments[0] = 0.08
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill
    if line_color is None:
        shape.line.fill.background()
    else:
        shape.line.color.rgb = line_color
        shape.line.width = Pt(line_width)
    shape.shadow.inherit = False
    return shape


def pill(slide, x, y, w, h, text, color=ACCENT, size=11, fill=None):
    shape = rect(slide, x, y, w, h, fill=fill or BG_CARD,
                 line_color=color, radius=True)
    tf = shape.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = Inches(0.05)
    tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = True
    run.font.name = FONT
    run.font.color.rgb = color
    _set_ea(run)
    return shape


def arrow(slide, x, y, w, h, color=LINE, direction="right"):
    shape_type = (MSO_SHAPE.RIGHT_ARROW if direction == "right"
                  else MSO_SHAPE.DOWN_ARROW)
    shape = slide.shapes.add_shape(shape_type, Inches(x), Inches(y),
                                   Inches(w), Inches(h))
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.fill.background()
    shape.shadow.inherit = False
    return shape


def card(slide, x, y, w, h, title, lines, accent=ACCENT, title_size=14,
         body_size=11, fill=BG_CARD):
    rect(slide, x, y, w, h, fill=fill)
    rect(slide, x + 0.22, y + 0.2, 0.5, 0.055, fill=accent, line_color=None,
         radius=False)
    tx(slide, x + 0.22, y + 0.32, w - 0.44, 0.4, title, size=title_size,
       bold=True, color=TEXT)
    tx(slide, x + 0.22, y + 0.78, w - 0.44, h - 0.9,
       "\n".join("· " + s for s in lines), size=body_size, color=MUTED,
       spacing=5, line=1.3)


def content_slide(prs, kicker, title, page):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = BG
    rect(slide, 0, 0, SW, 0.09, fill=ACCENT_2, line_color=None, radius=False)
    tx(slide, 0.6, 0.34, 1.4, 0.3, kicker, size=11, bold=True, color=ACCENT)
    rect(slide, 0.6, 0.68, 0.62, 0.05, fill=ACCENT, line_color=None,
         radius=False)
    tx(slide, 0.6, 0.86, 11.9, 0.6, title, size=26, bold=True, color=TEXT)
    # 页脚
    tx(slide, 0.6, 7.06, 8.0, 0.25,
       "数智卫校 · 校园安全 AI 预警与干预系统", size=9, color=MUTED)
    tx(slide, 11.9, 7.06, 0.8, 0.25, f"{page:02d}", size=9, color=MUTED,
       align=PP_ALIGN.RIGHT)
    return slide


def table(slide, x, y, w, rows_data, col_widths, row_h=0.46, font_size=10,
          header_color=ACCENT):
    rows, cols = len(rows_data), len(rows_data[0])
    shape = slide.shapes.add_table(rows, cols, Inches(x), Inches(y),
                                   Inches(w), Inches(row_h * rows))
    tbl = shape.table
    tbl.first_row = True
    tbl.horz_banding = False
    for i, cw in enumerate(col_widths):
        tbl.columns[i].width = Inches(cw)
    for r in range(rows):
        tbl.rows[r].height = Inches(row_h)
        for c in range(cols):
            cell = tbl.cell(r, c)
            cell.margin_left = Inches(0.08)
            cell.margin_right = Inches(0.06)
            cell.margin_top = cell.margin_bottom = Inches(0.03)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            cell.fill.solid()
            cell.fill.fore_color.rgb = BG_CARD_2 if r % 2 else BG_CARD
            tf = cell.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.alignment = PP_ALIGN.LEFT if c == 0 else PP_ALIGN.CENTER
            run = p.add_run()
            run.text = str(rows_data[r][c])
            run.font.size = Pt(font_size)
            run.font.bold = (r == 0)
            run.font.name = FONT
            run.font.color.rgb = header_color if r == 0 else TEXT
            _set_ea(run)
    return tbl


def build(path: str) -> str:
    prs = Presentation()
    prs.slide_width = Inches(SW)
    prs.slide_height = Inches(SH)

    # ============ 1 封面 ============
    s = prs.slides.add_slide(prs.slide_layouts[6])
    s.background.fill.solid()
    s.background.fill.fore_color.rgb = BG
    rect(s, 0, 0, 0.18, SH, fill=ACCENT, line_color=None, radius=False)
    rect(s, 0.95, 1.55, 1.7, 0.06, fill=ACCENT, line_color=None, radius=False)
    tx(s, 0.95, 1.78, 11.4, 0.45,
       "数智卫校 · 智慧校园安全治理平台", size=15, bold=True, color=ACCENT)
    tx(s, 0.95, 2.32, 11.6, 1.3,
       "校园安全 AI 预警与干预系统", size=44, bold=True, color=TEXT)
    tx(s, 0.95, 3.42, 11.6, 0.5,
       "基于 AI + 大数据的智慧校园防霸凌系统", size=20, color=MUTED)
    tx(s, 0.95, 4.28, 11.2, 0.9,
       "计算机视觉实时分析  ·  分级预警自动触发  ·  人工 / 智能干预闭环  ·  全流程事件留痕",
       size=13, color=TEXT, line=1.5)
    # 底部能力标签
    labels = [("行为检测", ACCENT), ("实时告警", ORANGE), ("干预闭环", ACCENT_2),
              ("全程留痕", BLUE)]
    x = 0.95
    for text, color in labels:
        pill(s, x, 5.62, 1.85, 0.44, text, color=color, size=11)
        x += 2.02
    tx(s, 0.95, 6.62, 6.0, 0.3, "v2.0.0   |   2026", size=11, color=MUTED)
    tx(s, 8.6, 6.62, 3.9, 0.3, "数智卫校 · 校园安全专项", size=11, color=MUTED,
       align=PP_ALIGN.RIGHT)

    # ============ 2 建设背景 ============
    s = content_slide(prs, "01 / BACKGROUND", "建设背景：校园安全治理的四道难题", 2)
    data = [
        ("看得见，看不懂", ["摄像头数量多，依赖人工盯屏", "夜间与楼道盲区事件发现滞后",
                        "事后翻查录像耗时耗力"], ORANGE),
        ("霸凌隐蔽，取证困难", ["围堵、推搡、言语威胁持续时间短", "学生不敢主动报告",
                          "缺乏客观行为证据链"], RED),
        ("发现与处置割裂", ["发现、上报、调度分散在不同系统", "响应慢、责任边界不清",
                        "处置过程无完整记录"], ACCENT_2),
        ("数据沉睡，难以复盘", ["事件停留在纸质记录或聊天工具", "无法做区域与时段趋势分析",
                          "难以支撑德育决策"], BLUE),
    ]
    x = 0.6
    for title, lines, color in data:
        card(s, x, 1.9, 2.95, 2.35, title, lines, accent=color, title_size=13,
             body_size=10)
        x += 3.06
    rect(s, 0.6, 4.62, 12.13, 1.62, fill=BG_CARD_2)
    tx(s, 0.95, 4.86, 1.6, 0.35, "核心命题", size=12, bold=True, color=ACCENT)
    tx(s, 0.95, 5.28, 11.4, 0.8,
       "把监控从「只能看」升级为「能识别、能告警、能处置、能复盘」，\n"
       "让校园安全治理从「事后追责」前移到「事中干预」。",
       size=16, color=TEXT, line=1.5)

    # ============ 3 系统定位 ============
    s = content_slide(prs, "02 / POSITIONING", "系统定位：从行为检测到干预闭环", 3)
    steps = [("行为检测", "姿态关键点\n危险词匹配\n目标检测", ACCENT),
             ("实时告警", "四级定级\n去重升级\n定向推送", ORANGE),
             ("分级研判", "证据归档\n处置建议\n报告生成", ACCENT_2),
             ("干预处置", "语音震慑\n安保调度\n现场处置", RED),
             ("闭环归档", "事件留痕\n审计记录\n趋势复盘", BLUE)]
    x = 0.6
    for i, (title, body, color) in enumerate(steps):
        rect(s, x, 2.0, 2.18, 1.5, fill=BG_CARD)
        rect(s, x, 2.0, 2.18, 0.07, fill=color, line_color=None, radius=False)
        tx(s, x + 0.16, 2.2, 1.9, 0.35, f"{i + 1:02d}  {title}", size=13,
           bold=True, color=color)
        tx(s, x + 0.16, 2.65, 1.9, 0.75, body, size=10, color=MUTED, line=1.35)
        if i < len(steps) - 1:
            arrow(s, x + 2.21, 2.62, 0.2, 0.26, color=LINE)
        x += 2.44
    card(s, 0.6, 3.82, 3.9, 2.1, "精准识别",
         ["COCO-17 姿态关键点几何判定", "摔倒 / 攻击 / 围堵三类信号",
          "多信号叠加判定疑似霸凌"], accent=ACCENT, body_size=10.5)
    card(s, 4.71, 3.82, 3.9, 2.1, "自动分级",
         ["红 / 橙 / 黄 / 蓝四级预警", "同设备同类型 60 秒去重",
          "红色自动语音震慑"], accent=ORANGE, body_size=10.5)
    card(s, 8.82, 3.82, 3.9, 2.1, "完整留痕",
         ["预警、事件、工单、推送统一归档", "干预时间线全程可追溯",
          "审计日志覆盖关键操作"], accent=BLUE, body_size=10.5)
    tx(s, 0.6, 6.2, 12.1, 0.65,
       "系统同时保留图书馆占座监测作为扩展场景，验证同一套预警 / 工单 / 审计底座的可扩展性。",
       size=11.5, color=MUTED, line=1.4)

    # ============ 4 总体架构 ============
    s = content_slide(prs, "03 / ARCHITECTURE", "总体架构：五层解耦，统一底座", 4)
    layers = [
        ("应用层", "统一态势看板：总览 · 预警 · 事件 · 工单 · 场景 · 设备 · 用户 · 审计 · 指标", ACCENT),
        ("业务层", "认证与 RBAC · 预警闭环 · 事件/工单/违规/推送归档 · 场景编排 · 指标审计", ACCENT_2),
        ("算法层", "视觉事件判定 · 分级告警引擎 · 危险词匹配 · 风险研判与报告", ORANGE),
        ("感知层", "监控关键点帧序列 · 对讲音频/转写 · YOLO 检测结果 · 占座事件", BLUE),
        ("数据层", "SQLite WAL · 配置与词库 · 音频资产 · JSONL 归档", MUTED),
    ]
    y = 1.92
    for i, (name, desc, color) in enumerate(layers):
        rect(s, 0.6, y, 12.13, 0.82, fill=BG_CARD if i % 2 == 0 else BG_CARD_2)
        rect(s, 0.6, y, 0.09, 0.82, fill=color, line_color=None, radius=False)
        tx(s, 0.9, y + 0.16, 1.5, 0.5, name, size=15, bold=True, color=color,
           anchor=MSO_ANCHOR.MIDDLE)
        tx(s, 2.45, y + 0.1, 10.0, 0.62, desc, size=11.5, color=TEXT,
           anchor=MSO_ANCHOR.MIDDLE, line=1.35)
        if i < len(layers) - 1:
            arrow(s, 6.55, y + 0.84, 0.22, 0.1, color=LINE, direction="down")
        y += 0.95
    tx(s, 0.6, 6.72, 12.1, 0.3,
       "设计原则：分层解耦 · 标准库优先 · 可插拔降级 · 一事一档 · 安全默认",
       size=10.5, color=MUTED)

    # ============ 5 技术栈 ============
    s = content_slide(prs, "04 / TECH STACK", "技术栈与部署形态", 5)
    card(s, 0.6, 1.9, 5.95, 2.15, "后端与数据",
         ["FastAPI + Uvicorn，自动生成 OpenAPI 文档",
          "SQLite（WAL）零部署，表结构可平移 MySQL / PostgreSQL",
          "PBKDF2-HMAC-SHA256 密码哈希 + Bearer 会话 Token"], accent=ACCENT,
         body_size=10.5)
    card(s, 6.78, 1.9, 5.95, 2.15, "前端与交互",
         ["原生 HTML / CSS / JS 单文件看板",
          "纯 SVG 图表，无外网 CDN，离线可用",
          "按角色渲染导航，响应式适配多分辨率"], accent=ACCENT_2,
         body_size=10.5)
    card(s, 0.6, 4.22, 5.95, 2.1, "AI 与算法",
         ["COCO-17 姿态关键点纯算法判定，零依赖可测试",
          "YOLO / ASR / LLM 适配器接入，缺失时自动降级",
          "能量 VAD + 危险词分级匹配 + 可选本地 Ollama"], accent=ORANGE,
         body_size=10.5)
    card(s, 6.78, 4.22, 5.95, 2.1, "部署与运维",
         ["python -m server.main 或 start.bat 一键启动",
          "单机可运行，无需外部中间件",
          "审计日志 + 运行指标 + 健康检查"], accent=BLUE,
         body_size=10.5)
    tx(s, 0.6, 6.55, 12.1, 0.35,
       "预置 5 类角色：系统管理员 / 安保负责人 / 安保值班员 / 班主任 / 演示访客",
       size=10.5, color=MUTED)

    # ============ 6 视觉识别 ============
    s = content_slide(prs, "05 / VISION", "核心能力一：计算机视觉行为识别", 6)
    tx(s, 0.6, 1.72, 12.1, 0.35,
       "输入：校园监控视频经姿态估计得到 COCO-17 关键点序列，算法层做纯几何判定，无需 GPU 即可运行。",
       size=11.5, color=MUTED)
    cards = [
        ("摔倒检测", ["躯干倾角 = 肩中点→髋中点与竖直方向夹角", "θ > 65° 判定摔倒",
                  "角度越接近 90°，置信度越高"], ORANGE, "fall_angle = 65°"),
        ("攻击检测", ["逐帧计算双手腕位移之和", "滑窗内计算位移 Z-Score",
                  "Z > 3.0 判定为突发攻击"], RED, "attack_z = 3.0"),
        ("围堵检测", ["任意两人肩中点距离与平均肩宽比较", "距离 < 3.5 × 肩宽判定围堵",
                  "人数越多、间距越近置信度越高"], YELLOW, "surround_ratio = 3.5"),
    ]
    x = 0.6
    for title, lines, color, formula in cards:
        rect(s, x, 2.25, 3.95, 3.15, fill=BG_CARD)
        rect(s, x, 2.25, 3.95, 0.07, fill=color, line_color=None, radius=False)
        tx(s, x + 0.25, 2.5, 3.4, 0.4, title, size=16, bold=True, color=color)
        tx(s, x + 0.25, 3.0, 3.45, 1.4,
           "\n".join("· " + v for v in lines), size=10.5, color=MUTED,
           spacing=6, line=1.35)
        pill(s, x + 0.25, 4.62, 2.2, 0.4, formula, color=color, size=9.5)
        x += 4.09
    rect(s, 0.6, 5.62, 12.13, 1.05, fill=BG_CARD_2)
    tx(s, 0.9, 5.8, 11.5, 0.75,
       "多信号叠加：攻击 + 围堵（或摔倒）→ 自动判定「疑似校园霸凌」并升级红色预警；\n"
       "单信号只按基础级别告警，避免误报升级。",
       size=12, color=TEXT, line=1.45)

    # ============ 7 分级预警 ============
    s = content_slide(prs, "06 / ALERTING", "核心能力二：分级预警与多信号升级", 7)
    rows = [
        ["信号组合", "预警类型", "级别", "自动动作"],
        ["仅攻击", "疑似暴力攻击", "红色", "语音震慑 + 领导通知"],
        ["仅摔倒", "人员摔倒", "橙色", "通知安保与班主任"],
        ["仅围堵", "多人围堵", "黄色", "推送值班员核实"],
        ["攻击 + 摔倒", "疑似校园霸凌", "红色", "震慑 + 安保调度"],
        ["攻击 + 围堵", "疑似校园霸凌", "红色", "震慑 + 安保调度"],
        ["摔倒 + 围堵", "多信号冲突", "红色", "安保到场 + 通知"],
        ["三类同时出现", "疑似校园霸凌", "红色", "启动干预预案"],
    ]
    table(s, 0.6, 1.85, 7.4, rows, [1.55, 1.9, 0.9, 3.05], row_h=0.44,
          font_size=9.5)
    card(s, 8.25, 1.85, 4.45, 1.9, "去重与降噪",
         ["去重键：摄像头 + 类型", "窗口 60 秒，可配置",
          "避免同一事件刷屏"], accent=ACCENT, body_size=10)
    card(s, 8.25, 3.92, 4.45, 1.9, "自动震慑",
         ["红色预警自动排队语音干预", "固定警告话术：立即停止危险行为",
          "同步推送安保与班主任"], accent=RED, body_size=10)
    tx(s, 0.6, 6.05, 12.1, 0.8,
       "级别含义：红（紧急，可能正在发生霸凌/暴力）· 橙（高风险，立即核实）· 黄（需关注）· 蓝（提示观察）",
       size=10.5, color=MUTED, line=1.4)

    # ============ 8 干预闭环 ============
    s = content_slide(prs, "07 / CLOSED LOOP", "核心能力三：干预处置与全程留痕", 8)
    states = [("待受理", "pending", ACCENT), ("已受理", "acknowledged", ACCENT_2),
              ("干预中", "intervening", ORANGE), ("已闭环", "closed", BLUE)]
    x = 1.15
    for i, (label, code, color) in enumerate(states):
        rect(s, x, 1.95, 2.15, 0.95, fill=BG_CARD)
        rect(s, x, 1.95, 2.15, 0.06, fill=color, line_color=None, radius=False)
        tx(s, x, 2.08, 2.15, 0.3, label, size=13, bold=True, color=color,
           align=PP_ALIGN.CENTER)
        tx(s, x, 2.42, 2.15, 0.26, code, size=9, color=MUTED,
           align=PP_ALIGN.CENTER)
        if i < len(states) - 1:
            arrow(s, x + 2.2, 2.3, 0.42, 0.24, color=LINE)
        x += 2.66
    methods = [
        ("语音震慑", "自动播报警告话术，第一时间制止行为", RED),
        ("安保调度", "按级别推送安保负责人与值班领导", ORANGE),
        ("班主任通知", "同步学生所属班级，便于事后跟进", ACCENT),
        ("现场处置", "值班人员到场核实并填写处置结果", BLUE),
    ]
    x = 0.6
    for title, desc, color in methods:
        card(s, x, 3.35, 2.95, 1.6, title, [desc], accent=color,
             title_size=13, body_size=10)
        x += 3.06
    rect(s, 0.6, 5.25, 12.13, 1.35, fill=BG_CARD_2)
    tx(s, 0.9, 5.45, 11.5, 1.0,
       "留痕设计：每条预警拥有唯一编号 ALM-YYYYMMDD-xxxxxxxx，串起事件 EVT-*、工单、推送、\n"
       "干预时间线与审计日志，做到「一事一档、全程可溯」，支持一键生成研判报告。",
       size=11.5, color=TEXT, line=1.45)

    # ============ 9 多模态研判 ============
    s = content_slide(prs, "08 / MULTIMODAL", "核心能力四：多模态研判与智能报告", 9)
    nodes = [("音频 / 文本", "对讲收音\n转写文本", ACCENT), ("VAD 切分", "能量端点\n25ms 帧", ACCENT_2),
             ("危险词匹配", "红 / 橙 / 黄\n分级命中", ORANGE), ("风险研判", "规则建议\n可选 LLM", RED),
             ("报告与推送", "结构化归档\n定向通知", BLUE)]
    x = 0.6
    for i, (title, body, color) in enumerate(nodes):
        rect(s, x, 2.0, 2.1, 1.35, fill=BG_CARD)
        rect(s, x, 2.0, 2.1, 0.06, fill=color, line_color=None, radius=False)
        tx(s, x, 2.16, 2.1, 0.3, title, size=12, bold=True, color=color,
           align=PP_ALIGN.CENTER)
        tx(s, x, 2.55, 2.1, 0.65, body, size=9.5, color=MUTED,
           align=PP_ALIGN.CENTER, line=1.3)
        if i < len(nodes) - 1:
            arrow(s, x + 2.14, 2.55, 0.28, 0.22, color=LINE)
        x += 2.42
    card(s, 0.6, 3.75, 3.9, 1.95, "危险词分级",
         ["红：救命、打死、流血", "橙：别打我、滚开、住手",
          "黄：威胁、辱骂、挑衅"], accent=RED, body_size=10)
    card(s, 4.71, 3.75, 3.9, 1.95, "结构化研判",
         ["事件概况 + 证据 + 指标", "处置建议按类型与级别生成",
          "输出 Markdown 研判报告"], accent=ORANGE, body_size=10)
    card(s, 8.82, 3.75, 3.9, 1.95, "定向推送",
         ["按级别选择推送对象", "红色：领导 + 班主任",
          "橙/黄：值班员 + 班主任"], accent=BLUE, body_size=10)
    tx(s, 0.6, 6.0, 12.1, 0.7,
       "LLM 采用可选增强：本地 Ollama 可用时做深度研判，不可用时静默降级为规则结果，主流程不受影响。",
       size=10.5, color=MUTED, line=1.4)

    # ============ 10 功能模块 ============
    s = content_slide(prs, "09 / MODULES", "功能模块地图：一个看板，九个工作面", 10)
    modules = [
        ("01", "态势总览", "指标卡 · 24 小时趋势 · 级别/类型分布 · 摄像头状态"),
        ("02", "预警中心", "多条件筛选 · 证据与骨架 · 受理/干预/闭环"),
        ("03", "事件中心", "五类事件统一归档 · 按角色脱敏"),
        ("04", "工单闭环", "图书馆占座工单 · 一键闭环"),
        ("05", "智能场景", "8 个可运行演示场景 · 一键复现"),
        ("06", "设备管理", "摄像头/对讲/告警器台账与在线状态"),
        ("07", "用户与权限", "五类角色 · 权限点控制"),
        ("08", "审计日志", "登录与关键操作全量留痕"),
        ("09", "运行指标", "接口计数 · 登录情况 · 数据统计"),
    ]
    x0, y0 = 0.6, 1.85
    for i, (num, title, desc) in enumerate(modules):
        col, row = i % 3, i // 3
        x = x0 + col * 4.09
        y = y0 + row * 1.65
        rect(s, x, y, 3.9, 1.42, fill=BG_CARD if row % 2 == 0 else BG_CARD_2)
        tx(s, x + 0.2, y + 0.16, 0.6, 0.3, num, size=12, bold=True, color=ACCENT)
        tx(s, x + 0.75, y + 0.14, 2.9, 0.32, title, size=13, bold=True, color=TEXT)
        tx(s, x + 0.2, y + 0.62, 3.5, 0.7, desc, size=9.5, color=MUTED, line=1.35)

    # ============ 11 业务流程 ============
    s = content_slide(prs, "10 / WORKFLOW", "业务流程：从画面到闭环的六个动作", 11)
    steps = [
        ("01", "画面接入", "摄像头关键点\n帧序列上送"),
        ("02", "行为识别", "摔倒 / 攻击 /\n围堵判定"),
        ("03", "分级定级", "多信号升级\n60 秒去重"),
        ("04", "实时告警", "生成 ALM 编号\n定向推送"),
        ("05", "干预处置", "震慑 / 调度 /\n通知 / 到场"),
        ("06", "闭环归档", "状态 closed\n报告与审计"),
    ]
    x = 0.6
    for i, (num, title, desc) in enumerate(steps):
        rect(s, x, 2.0, 1.85, 1.65, fill=BG_CARD)
        tx(s, x + 0.18, 2.14, 0.6, 0.3, num, size=11, bold=True, color=ACCENT)
        tx(s, x + 0.18, 2.5, 1.55, 0.3, title, size=12, bold=True, color=TEXT)
        tx(s, x + 0.18, 2.9, 1.55, 0.65, desc, size=9, color=MUTED, line=1.3)
        if i < len(steps) - 1:
            arrow(s, x + 1.88, 2.68, 0.22, 0.22, color=LINE)
        x += 2.07
    rows = [
        ["阶段", "系统承载", "输出"],
        ["行为检测", "ai/vision · ai/audio · behavior_service", "风险信号"],
        ["实时告警", "ai/alert_engine · server/api.py", "预警 ALM-*"],
        ["干预处置", "预警闭环 API · interventions 表", "干预时间线"],
        ["闭环归档", "alerts / events / audit_logs", "事件 EVT-*"],
    ]
    table(s, 0.6, 4.1, 12.1, rows, [2.4, 6.2, 3.5], row_h=0.42, font_size=9.5)
    tx(s, 0.6, 6.55, 12.1, 0.35,
       "编号规范：ALM-YYYYMMDD-xxxxxxxx（预警） · EVT-YYYYMMDD-xxxxxxxx（事件）",
       size=10, color=MUTED)

    # ============ 12 演示场景 ============
    s = content_slide(prs, "11 / SCENARIOS", "演示场景：八个可一键复现的业务现场", 12)
    scenarios = [
        ("视觉 · 暴力攻击", "红色预警 + 自动语音震慑", RED),
        ("视觉 · 人员摔倒", "橙色预警 + 校医/班主任通知", ORANGE),
        ("视觉 · 多人围堵", "黄色预警 + 值班员核实", YELLOW),
        ("视觉 · 霸凌冲突", "围堵 + 攻击叠加，升级红色", RED),
        ("智能体 · 多模态研判", "转写 + 研判 + 报告 + 定向推送", ACCENT),
        ("对讲 · 危险词干预", "命中危险词告警并播放制止话术", ACCENT_2),
        ("行为规范 · 人脸定位", "违规检出 + 定位 + 未识别转人工", BLUE),
        ("图书馆 · 占座工单", "超时占座生成工单并推送管理员", MUTED),
    ]
    for i, (title, desc, color) in enumerate(scenarios):
        col, row = i % 2, i // 2
        x = 0.6 + col * 6.12
        y = 1.9 + row * 1.22
        rect(s, x, y, 5.95, 1.02, fill=BG_CARD if row % 2 == 0 else BG_CARD_2)
        rect(s, x, y, 0.08, 1.02, fill=color, line_color=None, radius=False)
        tx(s, x + 0.28, y + 0.16, 5.4, 0.32, title, size=12.5, bold=True,
           color=TEXT)
        tx(s, x + 0.28, y + 0.56, 5.4, 0.3, desc, size=9.5, color=MUTED)

    # ============ 13 应用价值 ============
    s = content_slide(prs, "12 / VALUE", "应用价值：安全、管理、技术与合规", 13)
    values = [
        ("安全价值", ["事后追责前移为事中干预", "红色预警分钟级响应",
                  "语音震慑第一时间制止"], RED),
        ("管理价值", ["五类事件统一归档", "区域与时段趋势可分析",
                  "支撑安全例会与德育决策"], ACCENT),
        ("技术价值", ["算法与业务解耦", "姿态判定零依赖可测试",
                  "视觉模型可按硬件替换"], ACCENT_2),
        ("合规价值", ["最小权限与敏感字段脱敏", "关键操作全量审计",
                  "符合个人信息保护要求"], BLUE),
    ]
    x = 0.6
    for title, lines, color in values:
        card(s, x, 1.9, 2.95, 2.45, title, lines, accent=color, title_size=14,
             body_size=10)
        x += 3.06
    kpis = [("63", "项自动化测试通过", ACCENT), ("8", "个可运行演示场景", ACCENT_2),
            ("4", "级预警分级", ORANGE), ("9", "张业务数据表", BLUE)]
    x = 0.6
    for num, label, color in kpis:
        rect(s, x, 4.7, 2.95, 1.15, fill=BG_CARD_2)
        tx(s, x + 0.2, 4.84, 0.9, 0.6, num, size=26, bold=True, color=color)
        tx(s, x + 1.1, 4.98, 1.75, 0.6, label, size=10, color=MUTED, line=1.3)
        x += 3.06
    tx(s, 0.6, 6.15, 12.1, 0.55,
       "面向中职 / 高职校园安全治理场景，可作为数智卫校安全大脑的核心业务子系统。",
       size=11.5, color=MUTED, line=1.4)

    # ============ 14 交付与展望 ============
    s = content_slide(prs, "13 / DELIVERY", "交付清单与后续演进", 14)
    card(s, 0.6, 1.9, 5.95, 3.0, "本次交付",
         ["清理后的完整代码工程（统一技术栈）",
          "8 份标准化项目文档（概述/架构/功能/流程/算法/API/部署/验收）",
          "PPTX 与 HTML 双版本演示稿",
          "63 项自动化测试与 8 个演示场景",
          "真实语音资产与旧项目合规资料"], accent=ACCENT, body_size=10.5)
    card(s, 6.78, 1.9, 5.95, 3.0, "后续演进",
         ["接入 RTMPose / YOLO-Pose 实时视频流",
          "ASR 与本地大模型网关接入，提升研判深度",
          "数据库迁移 MySQL / PostgreSQL，支撑多校区",
          "对接教育局与公安平台，实现跨系统级联",
          "移动端巡查与家校通知"], accent=ACCENT_2, body_size=10.5)
    rect(s, 0.6, 5.2, 12.13, 1.15, fill=BG_CARD_2)
    tx(s, 0.9, 5.4, 11.5, 0.75,
       "让每一次危险行为都被看见，让每一次预警都有回应。\n"
       "数智卫校 · 校园安全 AI 预警与干预系统",
       size=15, bold=True, color=TEXT, align=PP_ALIGN.CENTER, line=1.5)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    prs.save(path)
    return path


if __name__ == "__main__":
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    output = os.path.join(root, "ppt",
                          "数智卫校_校园安全AI预警与干预系统_演示文稿.pptx")
    saved = build(output)
    print("已生成：", saved)
