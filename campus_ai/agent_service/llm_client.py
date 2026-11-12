"""本地大模型客户端：Ollama 深判 + 离线规则兜底。

结构化输出统一走字段校验，非法值自动降级，保证报告格式稳定可展示。
"""
from __future__ import annotations

import json
import re
import urllib.request
from typing import Any, Protocol

from .parsers import MediaBundle, ParsedMedia

REPORT_KEYS = ["event_type", "severity", "summary", "timeline",
               "involved_count", "suggestion"]


class LLMClient(Protocol):
    def analyze(self, bundle: MediaBundle, parsed: ParsedMedia) -> dict[str, Any]: ...


def build_prompt(bundle: MediaBundle, parsed: ParsedMedia) -> str:
    return (
        "你是校园安全分析专家。基于以下多模态素材，输出严格 JSON：\n"
        '{"event_type": 事件类型, "severity": red/orange/yellow/blue, '
        '"summary": 一句话摘要, "timeline": [关键时刻描述], '
        '"involved_count": 涉及人数估计, "suggestion": 处置建议}\n'
        f"事件ID: {bundle.event_id}\n地点: {bundle.location}\n"
        f"语音转写: {parsed.transcript or '（无）'}\n"
        f"姿态数据帧数: {len(parsed.pose_summary)}\n"
        f"视频元数据: {json.dumps(parsed.video_meta, ensure_ascii=False) or '（无）'}\n"
        "只输出 JSON，不要多余文本。"
    )


def validate_report(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    out["event_type"] = str(data.get("event_type") or "unknown")
    severity = str(data.get("severity") or "blue").lower()
    out["severity"] = severity if severity in ("red", "orange", "yellow", "blue") else "blue"
    out["summary"] = str(data.get("summary") or "（模型未给出摘要）")
    timeline = data.get("timeline")
    out["timeline"] = [str(x) for x in timeline] if isinstance(timeline, list) else []
    try:
        out["involved_count"] = max(0, int(data.get("involved_count") or 0))
    except (TypeError, ValueError):
        out["involved_count"] = 0
    out["suggestion"] = str(data.get("suggestion") or "请值班人员核实现场情况。")
    return out


def extract_json(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("模型输出中未找到 JSON 对象")
    return json.loads(match.group(0))


class OllamaClient:
    def __init__(self, base_url: str = "http://localhost:11434",
                 model: str = "qwen2.5-vl", timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def analyze(self, bundle: MediaBundle, parsed: ParsedMedia) -> dict[str, Any]:
        payload = json.dumps({
            "model": self.model,
            "prompt": build_prompt(bundle, parsed),
            "stream": False,
            "format": "json",
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/generate", data=payload,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return validate_report(extract_json(body.get("response", "")))


class MockLLMClient:
    """离线规则化分析：按转写文本命中级别推断严重度，保证全流程可测。"""

    RULES = [
        ("red", ["救命", "报警", "110", "帮帮我", "打人"]),
        ("orange", ["别打我", "打死你", "弄死你", "找死", "放过我"]),
        ("yellow", ["废物", "垃圾", "滚", "傻"]),
    ]

    def analyze(self, bundle: MediaBundle, parsed: ParsedMedia) -> dict[str, Any]:
        text = parsed.transcript
        severity = "blue"
        for level, words in self.RULES:
            if any(word in text for word in words):
                severity = level
                break
        persons = len({frame.get("person_id") for frame in parsed.pose_summary
                       if isinstance(frame, dict) and frame.get("person_id") is not None})
        return validate_report({
            "event_type": "bullying_suspected" if severity in ("red", "orange") else "noise",
            "severity": severity,
            "summary": f"{bundle.location} 检测到疑似"
                       f"{'严重' if severity == 'red' else '一般'}冲突语音",
            "timeline": [f"语音转写命中关键词，片段长度 {len(text)} 字"] if text else [],
            "involved_count": persons,
            "suggestion": "建议安保人员立即到场核实" if severity in ("red", "orange")
                          else "建议关注该区域动态",
        })
