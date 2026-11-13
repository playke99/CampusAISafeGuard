"""全局配置 —— 从 settings.json 加载, 阈值可通过 API 在线修改"""
import json
import threading
from pathlib import Path
from typing import Optional

DEFAULTS = {
    # ── 视频源: "0"=本机摄像头索引, "rtsp://..."=网络摄像头, "*.mp4"=本地文件 ──
    "source": "0",
    "camera_id": "lib-cam-01",
    "zone_prefix": "图书馆自习区",
    # ── 模型 ──
    "pose_weights": "",                 # 姿态权重(人+关键点), 留空则降级用检测权重
    "detect_weights": "weights/yolo26n.pt",   # 物品检测(COCO), 不存在则自动降级
    "conf": 0.35,
    # ── 占座判定阈值 ──
    "grace_seconds": 180,            # 暂离宽限期(秒): 离开未超过此时长不清零也不计嫌疑
    "absent_threshold_minutes": 25,  # 占座判定阈值(分钟), 建议 20~30
    "seat_thresholds": {},           # 按座位覆盖阈值 {"A01": 15}, 未配置则用全局阈值
    "absent_confirm_frames": 3,      # 连续 N 次检测无人才确认"人不在"(防单帧漏检)
    "require_items": True,           # 必须"物品在+人不在"才计时(无检测权重时自动降级为 False)
    "detect_interval_sec": 1.0,      # 检测频率(秒)
    # ── 帧预处理 ──
    "preprocess": {                  # 缩放/光照均衡/去噪；cv2 缺失时自动直通
        "resize_width": 960,
        "clahe": True,
        "clahe_clip": 2.0,
        "denoise": False,
    },
    # ── 背景建模（遗留物辅助判定） ──
    "background": {
        "enabled": True,
        "method": "auto",            # auto / mog2 / framediff
        "history": 240,
        "var_threshold": 32.0,
        "diff_threshold": 22.0,
        "occupied_ratio": 0.10,      # ROI 前景占比超过该值视为"区域有物/有人"
        "static_window_sec": 30.0,   # 静态遗留物观察窗(秒)
        "static_stability": 0.05,    # 窗口内占比极差小于该值判定为静态
        "assist_items": True,        # 静态遗留物补充"物品在"证据
    },
    # ── 下游链路 ──
    "agent_url": "http://127.0.0.1:8100",
    "agent_token": "campus-local",
    "http_port": 8200,
}


class Settings:
    """线程安全的配置读写(threshold 等 API 可热更新)"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data = dict(DEFAULTS)
        self.reload()

    def reload(self):
        if self.path.exists():
            with open(self.path, "r", encoding="utf-8") as f:
                stored = json.load(f)
            with self._lock:
                self._data.update(stored)

    def get(self, key: str):
        with self._lock:
            return self._data.get(key, DEFAULTS.get(key))

    def update(self, patch: dict) -> dict:
        with self._lock:
            for k, v in patch.items():
                if k in DEFAULTS:
                    self._data[k] = v
            data = dict(self._data)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return data

    def all(self) -> dict:
        with self._lock:
            return dict(self._data)

    def resolve(self, rel: str) -> Path:
        """相对路径 -> 基于 settings.json 所在目录的绝对路径"""
        p = Path(rel)
        return p if p.is_absolute() else (self.path.parent / p).resolve()


def load_settings(path: Optional[str] = None) -> Settings:
    cfg_path = Path(path) if path else Path(__file__).resolve().parent / "settings.json"
    return Settings(cfg_path)
