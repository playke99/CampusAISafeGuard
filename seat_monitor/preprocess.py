"""帧预处理 —— 缩放 / 光照均衡 / 去噪

职责边界
--------
只负责"单帧图像 -> 更适合检测的图像"，不感知线程与状态机。
cv2 惰性导入：未安装 opencv 时 apply() 原样直通，流水线不中断。

处理顺序
--------
resize_width 等比缩放(仅缩小) -> CLAHE 光照均衡(LAB 的 L 通道) -> 可选去噪。
预处理帧是唯一推理输入：检测框、ROI 命中、标注帧、快照全部基于预处理坐标系，
ROI 为归一化多边形，缩放不影响区域命中。
"""
import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULTS = {
    "resize_width": 960,     # 等比缩放到该宽度；0/None 表示不缩放
    "clahe": True,           # CLAHE 光照均衡(逆光/昏暗场景)
    "clahe_clip": 2.0,
    "clahe_grid": 8,
    "denoise": False,        # 非局部均值去噪(耗时长，默认关)
}


def _import_cv2():
    try:
        import cv2
        return cv2
    except ImportError:
        return None


class FramePreprocessor:
    """可注入推理线程的帧预处理器；任何异常都降级为原帧直通"""

    def __init__(self, cfg: Optional[dict] = None):
        cfg = {**DEFAULTS, **(cfg or {})}
        self.resize_width = int(cfg.get("resize_width") or 0)
        self.clahe = bool(cfg.get("clahe"))
        self.clahe_clip = float(cfg.get("clahe_clip", 2.0))
        self.clahe_grid = int(cfg.get("clahe_grid", 8))
        self.denoise = bool(cfg.get("denoise"))
        self._cv2 = _import_cv2()
        self._clahe = None
        if self._cv2 is not None and self.clahe:
            try:
                self._clahe = self._cv2.createCLAHE(
                    clipLimit=self.clahe_clip,
                    tileGridSize=(self.clahe_grid, self.clahe_grid))
            except Exception as e:
                logger.warning("CLAHE 初始化失败(已关闭光照均衡): %s", e)
                self._clahe = None
        self._status = {
            "processed": 0,
            "fallbacks": 0,
            "last_ms": None,
            "in_shape": None,
            "out_shape": None,
            "cv2_available": self._cv2 is not None,
        }

    def apply(self, frame: Any) -> Any:
        """返回处理后的帧；无 cv2 或处理异常时原样返回"""
        if frame is None or self._cv2 is None:
            self._status["fallbacks"] += 1
            return frame
        cv2 = self._cv2
        started = time.time()
        try:
            h, w = frame.shape[:2]
            self._status["in_shape"] = [int(w), int(h)]
            out = frame
            if self.resize_width and w > self.resize_width:
                scale = self.resize_width / float(w)
                out = cv2.resize(out, (self.resize_width, int(h * scale)),
                                 interpolation=cv2.INTER_AREA)
            if self._clahe is not None:
                lab = cv2.cvtColor(out, cv2.COLOR_BGR2LAB)
                l, a, b = cv2.split(lab)
                l = self._clahe.apply(l)
                out = cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)
            if self.denoise:
                out = cv2.fastNlMeansDenoisingColored(out, None, 5, 5, 7, 15)
            self._status["processed"] += 1
            self._status["last_ms"] = round((time.time() - started) * 1000, 1)
            oh, ow = out.shape[:2]
            self._status["out_shape"] = [int(ow), int(oh)]
            return out
        except Exception as e:
            self._status["fallbacks"] += 1
            logger.warning("预处理异常(已原样直通): %s", e)
            return frame

    def stats(self) -> dict:
        return dict(self._status)
