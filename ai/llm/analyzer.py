"""风险深判与处置建议：规则化分析器。

对外统一输出：
- recommendations(alert_type, severity, evidence) -> list[str]
- deep_report(alert) -> Markdown 文本

本地大模型深判由 campus_ai/agent_service 的 OllamaClient 承担（可离线降级）。
"""
from __future__ import annotations

import json
from typing import Any

TYPE_LABELS = {
    "attack": "疑似暴力攻击",
    "fall": "人员摔倒",
    "surround": "多人围堵",
    "bullying": "疑似校园霸凌",
    "danger_word": "危险言语告警",
    "seat": "图书馆占座",
    "behavior": "行为规范违规",
}

SEVERITY_LABELS = {"red": "红色（紧急）", "orange": "橙色（较重）",
                   "yellow": "黄色（关注）", "blue": "蓝色（提示）"}

BASE_ADVICE = {
    "attack": ["立即调度最近安保人员赶赴现场", "调取前后 30 秒监控视频固定证据",
               "通知涉事学生班主任到场", "视伤情拨打 120 / 110"],
    "fall": ["通知校医与值班教师前往查看", "确认人员意识与伤情，必要时拨打 120",
             "保留现场视频，排查摔倒原因"],
    "surround": ["派员前往核实是否存在冲突", "通过广播或对讲进行现场提醒",
                 "持续观察人群聚集变化"],
    "bullying": ["启动校园霸凌红色处置预案", "安保负责人与值班领导同步到场",
                 "隔离涉事双方，通知家长与班主任", "完整留存视频与语音证据链"],
    "danger_word": ["通知安保值班员核实现场情况", "调取对讲位置附近监控",
                    "必要时播放制止话术"],
    "seat": ["推送图书管理员核实座位使用情况", "按图书馆占座规则清理并登记"],
    "behavior": ["推送纪检与班主任进行提醒教育", "人脸未匹配记录转人工复核"],
}


class RiskAnalyzer:
    def recommendations(self, alert_type: str, severity: str,
                        evidence: dict[str, Any] | None = None) -> list[str]:
        advice = list(BASE_ADVICE.get(alert_type, ["通知值班人员核实现场情况"]))
        if severity == "red":
            advice.insert(0, "红色预警：同步推送安保负责人与值班校领导")
        elif severity == "orange":
            advice.insert(0, "橙色预警：5 分钟内完成现场核实并反馈")
        evidence = evidence or {}
        if evidence.get("transcript"):
            advice.append(f"语音证据：{evidence['transcript'][:60]}")
        return advice

    def deep_report(self, alert: dict[str, Any]) -> str:
        evidence = alert.get("evidence") or {}
        advice = evidence.get("advice") or self.recommendations(
            alert.get("alert_type", ""), alert.get("severity", "blue"), evidence)
        signal_text = evidence.get("signal_text") or "无"
        lines = [
            "# 校园安全事件分析报告",
            "",
            f"- 预警编号：{alert.get('alert_id', '')}",
            f"- 事件编号：{alert.get('event_id', '')}",
            f"- 事件类型：{TYPE_LABELS.get(alert.get('alert_type', ''), alert.get('alert_type', ''))}",
            f"- 预警级别：{SEVERITY_LABELS.get(alert.get('severity', ''), alert.get('severity', ''))}",
            f"- 发生地点：{alert.get('location', '')}",
            f"- 设备编号：{alert.get('camera_id', '')}",
            f"- 模型置信度：{alert.get('confidence', 0)}",
            f"- 检测信号：{signal_text}",
            "",
            "## 事件摘要",
            alert.get("summary", ""),
            "",
            "## 处置建议",
        ]
        lines += [f"{i}. {item}" for i, item in enumerate(advice, 1)]
        lines += ["", "## 证据留档", "```json",
                  json.dumps(evidence, ensure_ascii=False, indent=2), "```"]
        return "\n".join(lines)
