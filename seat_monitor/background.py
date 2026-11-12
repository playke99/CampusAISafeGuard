"""背景建模 —— 前景提取与"静态遗留物"辅助判定

职责
----
1. 维护场景背景模型，输出前景掩码：
   - cv2 可用时优先 MOG2（自动适应光照缓变）；
   - 无 cv2 时降级为 numpy 运行均值背景 + 帧差阈值化（零额外依赖）。
2. 对每个座位 ROI 计算前景占比 fg_ratio（网格采样，纯 Python 射线法）；
3. 维护 fg_ratio 时间窗：占比持续高于 occupied_ratio 且波动小于 static_stability
   判定为"静态遗留物"（static=True），供状态机补充"物品在"证据。

设计要点
--------
- 不参与"人/物分类"（分类由检测器负责），只回答"该区域是否有东西停留"；
- 逆光/昏暗场景依赖上游 FramePreprocessor 的 CLAHE，本模块只做灰度差分；
- 所有异常内部消化并降级（enabled=False 语义），不阻塞推理流水线。
"""
import logging
import time
from collections import deque
from typing import Deque, Dict, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

DEFAULTS = {
    "enabled": True,
    "method": "auto",          # auto / mog2 / framediff
    "history": 240,            # 背景学习窗口（帧）
    "var_threshold": 32.0,     # MOG2 方差阈值
    "diff_threshold": 22.0,    # 帧差灰度阈值(0-255)
    "blur": 5,                 # 灰度高斯模糊核(奇数，0/1 关闭)
    "occupied_ratio": 0.10,    # ROI 前景占比超过该值视为"区域有物/有人"
    "static_window_sec": 30.0, # 静态判定观察窗（秒）
    "static_stability": 0.05,  # 窗口内占比极差小于该值判定为静态遗留物
    "grid": 14,                # ROI 采样网格边长
}

# 与 seat_state._point_in_polygon 相同的射线法（避免循环依赖，独立实现）
def _inside(x: float, y: float, polygon) -> bool:
    if len(polygon) < 3:
        return False
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


class BackgroundModel:
    def __init__(self, cfg: Optional[dict] = None):
        cfg = {**DEFAULTS, **(cfg or {})}
        self.enabled = bool(cfg.get("enabled", True))
        self.method = str(cfg.get("method", "auto"))
        self.history = max(16, int(cfg.get("history", 240)))
        self.var_threshold = float(cfg.get("var_threshold", 32.0))
        self.diff_threshold = float(cfg.get("diff_threshold", 22.0))
        self.blur = int(cfg.get("blur", 5))
        if self.blur and self.blur % 2 == 0:
            self.blur += 1
        self.occupied_ratio = float(cfg.get("occupied_ratio", 0.10))
        self.static_window_sec = float(cfg.get("static_window_sec", 30.0))
        self.static_stability = float(cfg.get("static_stability", 0.05))
        self.grid = max(6, int(cfg.get("grid", 14)))

        self._cv2 = None                     # framediff 模式下保持 None
        self._mog2 = None
        self._bg: Optional[np.ndarray] = None      # 运行均值背景(float32 灰度)
        self._mask: Optional[np.ndarray] = None    # 最新前景掩码(uint8 0/255)
        self._regions: Dict[str, Deque[Tuple[float, float]]] = {}
        self._status = {
            "enabled": self.enabled,
            "backend": "none",
            "updates": 0,
            "last_ms": None,
            "mask_shape": None,
            "last_error": "",
        }
        if self.enabled:
            self._init_backend()

    # ── 后端初始化 ───────────────────────────────────────
    def _init_backend(self):
        if self.method in ("auto", "mog2"):
            try:
                import cv2
                self._cv2 = cv2
                self._mog2 = cv2.createBackgroundSubtractorMOG2(
                    history=self.history, varThreshold=self.var_threshold,
                    detectShadows=False)
                self._status["backend"] = "mog2"
                return
            except ImportError:
                if self.method == "mog2":
                    self._status["last_error"] = "未安装 opencv-python，降级为帧差法"
                self._cv2 = None
            except Exception as e:
                self._status["last_error"] = f"MOG2 初始化失败，降级为帧差法: {e}"
                self._cv2 = None
        self._status["backend"] = "framediff"

    # ── 灰度化 ───────────────────────────────────────────
    def _to_gray(self, frame) -> np.ndarray:
        if frame.ndim == 2:
            gray = frame.astype(np.float32)
        else:
            gray = frame.astype(np.float32).mean(axis=2)
        if self.blur and self._cv2 is not None:
            gray = self._cv2.GaussianBlur(
                gray.astype(np.uint8), (self.blur, self.blur), 0).astype(np.float32)
        return gray

    # ── 每帧更新 ─────────────────────────────────────────
    def update(self, frame) -> Optional[np.ndarray]:
        """更新背景模型并返回前景掩码；异常降级为 None"""
        if not self.enabled or frame is None:
            return None
        started = time.time()
        try:
            if self._mog2 is not None:
                gray = self._to_gray(frame).astype(np.uint8)
                mask = self._mog2.apply(gray)
                # MOG2 输出 127 为阴影/老背景，255 为前景；detectShadows=False 仍可能给出中间值
                mask = np.where(mask >= 200, 255, 0).astype(np.uint8)
            else:
                gray = self._to_gray(frame)
                if self._bg is None or self._bg.shape != gray.shape:
                    self._bg = gray.copy()
                    mask = np.zeros_like(gray, dtype=np.uint8)
                else:
                    diff = np.abs(gray - self._bg)
                    mask = np.where(diff > self.diff_threshold, 255, 0).astype(np.uint8)
                    alpha = 1.0 / self.history
                    # 缓慢学习：前景区域也纳入背景，遗留物最终会被"吸收"为背景
                    self._bg = (1.0 - alpha) * self._bg + alpha * gray
            self._mask = mask
            self._status["updates"] += 1
            self._status["last_ms"] = round((time.time() - started) * 1000, 1)
            self._status["mask_shape"] = [int(mask.shape[1]), int(mask.shape[0])]
            return mask
        except Exception as e:
            self._status["last_error"] = f"{type(e).__name__}: {e}"
            logger.warning("背景建模异常(本帧跳过): %s", e)
            return None

    # ── ROI 前景占比与静态判定 ────────────────────────────
    def region_signals(self, seat_id: str, polygon_norm, now: Optional[float] = None) -> dict:
        """返回 {"fg_ratio": 0~1, "static": bool}；mask 未就绪时 ratio 为 0"""
        now = time.time() if now is None else now
        ratio = 0.0
        if self._mask is not None:
            h, w = self._mask.shape[:2]
            pts = [(float(px) * w, float(py) * h) for px, py in polygon_norm]
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            x1, x2 = max(0.0, min(xs)), min(float(w - 1), max(xs))
            y1, y2 = max(0.0, min(ys)), min(float(h - 1), max(ys))
            hit = 0
            total = 0
            g = self.grid
            for iy in range(g):
                y = y1 + (y2 - y1) * (iy + 0.5) / g
                for ix in range(g):
                    x = x1 + (x2 - x1) * (ix + 0.5) / g
                    if not _inside(x, y, pts):
                        continue
                    total += 1
                    xi, yi = int(x), int(y)
                    if 0 <= yi < h and 0 <= xi < w and self._mask[yi, xi] > 0:
                        hit += 1
            if total:
                ratio = hit / total

        seq = self._regions.setdefault(seat_id, deque())
        seq.append((now, ratio))
        cutoff = now - self.static_window_sec
        while seq and seq[0][0] < cutoff:
            seq.popleft()
        ratios = [r for _, r in seq]
        static = (len(ratios) >= 5
                  and (sum(ratios) / len(ratios)) >= self.occupied_ratio
                  and (max(ratios) - min(ratios)) <= self.static_stability)
        return {"fg_ratio": round(ratio, 3), "static": bool(static)}

    def mask(self) -> Optional[np.ndarray]:
        return self._mask

    def stats(self) -> dict:
        st = dict(self._status)
        st["occupied_ratio"] = self.occupied_ratio
        st["regions"] = len(self._regions)
        return st
