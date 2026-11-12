"""标准化报告渲染：结构化 JSON → 统一格式的 Markdown 文本报告。"""
from __future__ import annotations

import time
from typing import Any

SEVERITY_CN = {"red": "红色（极高）", "orange": "橙色（高）",
               "yellow": "黄色（中）", "blue": "蓝色（提示）"}


def render_report(report: dict[str, Any], event_id: str, location: str,
                  ts: float | None = None) -> str:
    lines = [
        "# 校园安全事件分析报告",
        "",
        f"- 报告编号：{event_id}",
        f"- 事发地点：{location}",
        f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts)) if ts else time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 事件类型：{report['event_type']}",
        f"- 预警级别：{SEVERITY_CN.get(report['severity'], report['severity'])}",
        f"- 涉及人数：约 {report['involved_count']} 人",
        "",
        "## 事件摘要",
        report["summary"],
        "",
        "## 关键时间线",
    ]
    if report["timeline"]:
        lines += [f"{i + 1}. {item}" for i, item in enumerate(report["timeline"])]
    else:
        lines.append("（无）")
    lines += ["", "## 处置建议", report["suggestion"]]
    return "\n".join(lines)
