"""配置加载：JSON 文件与默认值深合并。

所有外部路径（词库、模型、存储目录）允许配置为相对项目根目录的路径，
由 `resolve_path` 统一解析，避免启动目录不同导致找不到文件。
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

DEFAULTS: dict[str, Any] = {
    "agent": {
        "use_ollama": False,
        "base_url": "http://localhost:11434",
        "model": "qwen2.5-vl",
        "timeout": 30.0,
        "location": "校园公共区域",
        "route": {
            "red": ["security_leader", "duty_principal", "head_teacher"],
            "orange": ["security_staff", "head_teacher"],
            "yellow": ["security_staff"],
            "blue": ["security_staff"],
        },
    },
    "intercom": {
        "device_id": "intercom-lib-01",
        "location": "图书馆3层自习区",
        "danger_words_file": "config/danger_words.json",
        "enable_tts": False,
        "deterrence_phrases": {
            "red": "警告！监测到紧急情况，安保人员正在赶来，请立即停止危险行为！",
            "orange": "请注意言行！你的行为已被记录，请立即停止，保持冷静！",
            "yellow": "温馨提示：请文明用语，和谐相处。",
        },
    },
    "seat_monitor": {
        "camera_id": "lib-cam-01",
        "location": "图书馆自习区",
        "default_threshold_minutes": 25,
        "staff_targets": ["library_staff"],
        "camera_url": "http://127.0.0.1:8200",
    },
    "behavior": {
        "camera_id": "teach-cam-01",
        "location": "教学楼A栋入口",
        "classes": ["slippers", "breakfast"],
        "conf_threshold": 0.5,
        "model_path": "campus_ai/weights/behavior.pt",
        "face_match_url": "",
        "face_match_token": "",
        "targets": ["discipline_office", "head_teacher"],
        "students": [
            {"student_id": "2023001", "name": "张三", "major": "软件技术", "class_name": "软件2301"},
            {"student_id": "2023002", "name": "李四", "major": "大数据技术", "class_name": "大数据2302"},
        ],
    },
    "push": {
        "endpoint": "",
        "token": "",
        "timeout": 3.0,
        "max_retry": 3,
    },
    "storage": {"root": "archive"},
}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def resolve_path(value: str | None) -> str:
    """相对路径按项目根目录解析为绝对路径。"""
    if not value:
        return ""
    path = Path(value)
    return str(path if path.is_absolute() else (PROJECT_ROOT / path))


def load_config(path: str | None = None) -> dict[str, Any]:
    """加载配置：深拷贝默认值后合并，调用方修改不会污染全局 DEFAULTS。"""
    base = copy.deepcopy(DEFAULTS)
    cfg_path = Path(path) if path else PROJECT_ROOT / "config" / "campus_config.json"
    if cfg_path.exists():
        with open(cfg_path, encoding="utf-8") as f:
            return _merge(base, json.load(f))
    return base
