"""应用配置：默认值 + config/app.json 深合并，路径统一相对项目根目录。"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULTS: dict[str, Any] = {
    "app": {
        "name": "数智卫校·校园安全 AI 预警与干预系统",
        "subtitle": "基于 AI + 大数据的智慧校园防霸凌平台",
        "version": "2.0.0",
        "host": "0.0.0.0",
        "port": 8100,
        "token_ttl_sec": 28800,
        "db_path": "data/campus_security.db",
        "web_dir": "web",
        "audio_dir": "audio_assets",
        "integration_token": "campus-local",
    },
    "vision": {
        "fall_angle": 65.0,
        "attack_z": 3.0,
        "surround_ratio": 3.5,
        "window_size": 8,
        "min_frames": 3,
        "dedupe_seconds": 60,
        "escalate_signals": 2,
    },
    "alert": {
        "auto_deterrence_red": True,
        "dispatch_roles": {
            "red": ["security_leader", "duty_principal", "head_teacher"],
            "orange": ["security_staff", "head_teacher"],
            "yellow": ["security_staff"],
            "blue": ["security_staff"],
        },
    },
    "campus_ai": {"config_path": "campus_config.json"},
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
    base = copy.deepcopy(DEFAULTS)
    cfg_path = Path(path) if path else PROJECT_ROOT / "config" / "app.json"
    if cfg_path.exists():
        with open(cfg_path, encoding="utf-8") as f:
            return _merge(base, json.load(f))
    return base


def save_config(config: dict[str, Any], path: str | None = None) -> None:
    cfg_path = Path(path) if path else PROJECT_ROOT / "config" / "app.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def db_path(config: dict[str, Any]) -> str:
    return resolve_path(config["app"]["db_path"])


def ensure_dirs(config: dict[str, Any]) -> None:
    for key in ("web_dir", "audio_dir"):
        path = resolve_path(config["app"].get(key))
        if path:
            Path(path).mkdir(parents=True, exist_ok=True)
    Path(db_path(config)).parent.mkdir(parents=True, exist_ok=True)


__all__ = ["PROJECT_ROOT", "DEFAULTS", "load_config", "save_config",
           "resolve_path", "db_path", "ensure_dirs"]
