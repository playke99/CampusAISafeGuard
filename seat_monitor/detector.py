"""YOLO 推理封装 —— 权重合法性校验 + 三级降级

职责边界
--------
本模块只负责"单帧图像 -> (人, 物品)"的纯推理，不感知线程、队列与状态机。
cv2 / ultralytics 全部惰性导入，缺少依赖时依然可以导入本模块并进入降级模式。

三级降级策略
------------
1. pose   : 姿态权重可用 —— 人检测 + 关键点（判定"人在不在座"）
2. fallback: 姿态权重不可用、检测权重可用 —— 用人框 + 框上半段估算落点
3. degraded: 两者都不可用 —— 返回空结果，状态引擎继续运行，不崩溃、不阻塞

权重合法性校验覆盖：未配置 / 路径不存在 / 不是文件 / 空文件或明显损坏 /
依赖缺失 / 模型加载异常。任何异常都会被记录到 status 并触发降级，绝不向上抛。
"""
import logging
import time
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# COCO 类别 id -> 中文物品名
ITEM_CLASSES = {
    24: "背包", 25: "雨伞", 26: "手提包", 28: "箱包",
    73: "书本", 41: "水杯", 39: "水瓶",
}

# COCO pose 上半身关键点索引: 0鼻 5/6肩 7/8肘
UPPER_BODY_KPTS = [0, 5, 6, 7, 8]

# 正常 YOLO 权重至少 1MB,+ 明显小于此值视为损坏/占位文件
MIN_WEIGHT_BYTES = 1024


class Person:
    __slots__ = ("bbox", "kpts")

    def __init__(self, bbox: Tuple[int, int, int, int], kpts: np.ndarray):
        self.bbox = bbox          # (x1,y1,x2,y2)
        self.kpts = kpts          # (17,3) x,y,conf

    def upper_body_points(self) -> List[Tuple[float, float]]:
        pts = []
        if self.kpts is not None and len(self.kpts) > 0:
            for i in UPPER_BODY_KPTS:
                x, y, c = self.kpts[i]
                if c > 0.3:
                    pts.append((float(x), float(y)))
        # 关键点缺失时兜底用人体框上半段中心
        if not pts:
            x1, y1, x2, y2 = self.bbox
            pts = [((x1 + x2) / 2, y1 + (y2 - y1) * 0.25)]
        return pts


class Item:
    __slots__ = ("bbox", "name", "conf")

    def __init__(self, bbox, name: str, conf: float):
        self.bbox = bbox
        self.name = name
        self.conf = conf


# ── 权重合法性校验 ───────────────────────────────────────
def validate_weight(path) -> dict:
    """校验单个权重文件，返回结构化结果（不抛异常）

    字段: path / exists / valid / size_bytes / error / loaded
    """
    info = {
        "path": str(path) if path else None,
        "exists": False,
        "valid": False,
        "size_bytes": 0,
        "error": "",
        "loaded": False,
    }
    if not path:
        info["error"] = "未配置权重路径"
        return info
    p = Path(path)
    try:
        if not p.exists():
            info["error"] = f"权重文件不存在: {p}"
            return info
        if not p.is_file():
            info["error"] = f"权重路径不是文件: {p}"
            return info
        size = p.stat().st_size
        info["exists"] = True
        info["size_bytes"] = size
        if size < MIN_WEIGHT_BYTES:
            info["error"] = f"权重文件疑似损坏或为空({size} 字节): {p}"
            return info
        info["valid"] = True
    except OSError as e:
        info["error"] = f"权重文件无法访问: {p} ({e})"
    return info


def _default_loader(path: str):
    """默认模型加载器；依赖缺失转为明确的中文错误"""
    try:
        from ultralytics import YOLO
    except ImportError as e:
        raise RuntimeError(
            "未安装 ultralytics，无法加载 YOLO 权重（pip install ultralytics）"
        ) from e
    return YOLO(path)


def _make_status(mode: str, pose_info: dict, detect_info: dict,
                 items_enabled: bool, message: str) -> dict:
    return {
        "mode": mode,
        "mode_cn": {
            "pose": "姿态关键点",
            "fallback": "人框降级",
            "degraded": "降级停用",
        }.get(mode, mode),
        "degraded": mode != "pose",
        "items_enabled": items_enabled,
        "pose": pose_info,
        "detect": detect_info,
        "message": message,
        "infer_errors": 0,
        "last_error": "",
        "last_infer_ms": None,
    }


class Detector:
    """已加载权重的推理器；推理异常内部消化，不向调用方抛出"""

    def __init__(self, pose_model, detect_model=None, pose_mode: bool = True,
                 conf: float = 0.35, status: Optional[dict] = None):
        self.pose = pose_model
        self.detect = detect_model
        self.pose_mode = pose_mode
        self.conf = conf
        self.status = status or _make_status(
            "pose" if pose_mode else "fallback", validate_weight(None),
            validate_weight(None), detect_model is not None, "",
        )

    @property
    def items_enabled(self) -> bool:
        return self.detect is not None

    # ── 错误记录（供 /api/health 展示） ────────────────────
    def _record_error(self, exc: Exception):
        self.status["infer_errors"] = int(self.status.get("infer_errors", 0)) + 1
        self.status["last_error"] = f"{type(exc).__name__}: {exc}"
        logger.warning("推理异常(已降级为空结果): %s", exc)

    def infer(self, frame: np.ndarray) -> Tuple[List[Person], List[Item]]:
        started = time.time()
        persons: List[Person] = []
        items: List[Item] = []

        try:
            res = self.pose.predict(frame, conf=self.conf, classes=[0],
                                    verbose=False)[0]
            kpts = None
            if self.pose_mode and res.keypoints is not None:
                kpts = res.keypoints.data.cpu().numpy()
            if res.boxes is not None and len(res.boxes):
                for i, box in enumerate(res.boxes.xyxy.cpu().numpy()):
                    persons.append(Person(
                        tuple(int(v) for v in box),
                        kpts[i] if kpts is not None else np.empty((0, 3))))
        except Exception as e:
            self._record_error(e)

        if self.detect is not None:
            try:
                cls_ids = list(ITEM_CLASSES.keys())
                res2 = self.detect.predict(frame, conf=self.conf, classes=cls_ids,
                                           verbose=False)[0]
                if res2.boxes is not None and len(res2.boxes):
                    names = res2.names
                    for box, cid, cf in zip(res2.boxes.xyxy.cpu().numpy(),
                                            res2.boxes.cls.cpu().numpy().astype(int),
                                            res2.boxes.conf.cpu().numpy()):
                        items.append(Item(tuple(int(v) for v in box),
                                          ITEM_CLASSES.get(cid, names.get(cid, "?")),
                                          float(cf)))
            except Exception as e:
                self._record_error(e)

        self.status["last_infer_ms"] = round((time.time() - started) * 1000, 1)
        return persons, items

    @staticmethod
    def annotate(frame: np.ndarray, persons: List[Person], items: List[Item],
                 rois: List[dict]) -> np.ndarray:
        """绘制调试画面: ROI 多边形 + 人框 + 物品框（缺 cv2 时原样返回）"""
        try:
            import cv2
        except ImportError:
            return frame
        vis = frame.copy()
        for roi in rois:
            pts = np.array([(int(x * vis.shape[1]), int(y * vis.shape[0]))
                            for x, y in roi["polygon"]], dtype=np.int32)
            color = (180, 120, 40)
            cv2.polylines(vis, [pts], True, color, 2)
            cx = int(np.mean([p[0] for p in pts]))
            cy = int(np.mean([p[1] for p in pts]))
            cv2.putText(vis, roi["seat_id"], (cx - 20, cy),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
        for p in persons:
            x1, y1, x2, y2 = p.bbox
            cv2.rectangle(vis, (x1, y1), (x2, y2), (80, 200, 80), 2)
        for it in items:
            x1, y1, x2, y2 = it.bbox
            cv2.rectangle(vis, (x1, y1), (x2, y2), (60, 160, 255), 2)
            cv2.putText(vis, it.name, (x1, max(12, y1 - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (60, 160, 255), 1)
        return vis


class DegradedDetector:
    """完全降级检测器：不加载任何模型，推理返回空结果，保证流水线不中断

    语义: "当前没有可用的视觉模型"，但状态引擎/队列/API 全部照常运行，
    前端会收到明确的中文错误提示，而不是服务无响应或崩溃。
    """

    def __init__(self, message: str, pose_info: Optional[dict] = None,
                 detect_info: Optional[dict] = None):
        self.pose_mode = False
        self.conf = 0.0
        self.message = message
        self.status = _make_status(
            "degraded", pose_info or validate_weight(None),
            detect_info or validate_weight(None), False, message)

    @property
    def items_enabled(self) -> bool:
        return False

    def infer(self, frame):
        return [], []

    @staticmethod
    def annotate(frame, persons, items, rois):
        return frame


def build_detector(pose_weights, detect_weights=None, conf: float = 0.35,
                   loader: Optional[Callable[[str], Any]] = None):
    """按"pose -> fallback -> degraded"顺序构建检测器，永不抛异常

    loader: 可注入的模型加载函数（测试用 FakeLoader 即可验证全部分支）
    """
    load = loader or _default_loader
    pose_info = validate_weight(pose_weights)
    detect_info = validate_weight(detect_weights)
    errors: List[str] = []

    # 1) 首选姿态模型
    pose_model = None
    if pose_info["valid"]:
        try:
            pose_model = load(str(pose_info["path"]))
            pose_info["loaded"] = True
        except Exception as e:
            pose_info["error"] = f"姿态权重加载失败: {e}"
            errors.append(pose_info["error"])
    else:
        errors.append(pose_info["error"])

    if pose_model is not None:
        detect_model = None
        if detect_info["valid"]:
            try:
                detect_model = load(str(detect_info["path"]))
                detect_info["loaded"] = True
            except Exception as e:
                detect_info["error"] = f"物品检测权重加载失败(已关闭物品检测): {e}"
                errors.append(detect_info["error"])
        message = "姿态关键点模型运行正常"
        message += "；物品检测已启用" if detect_model is not None else "；物品检测已关闭"
        if errors:
            message += "（部分降级：" + "；".join(errors) + "）"
        status = _make_status("pose", pose_info, detect_info,
                              detect_model is not None, message)
        return Detector(pose_model, detect_model, pose_mode=True,
                        conf=conf, status=status)

    # 2) 降级：用检测权重出人框（无关键点，精度下降）
    detect_model = None
    if detect_info["valid"]:
        try:
            detect_model = load(str(detect_info["path"]))
            detect_info["loaded"] = True
        except Exception as e:
            detect_info["error"] = f"降级检测权重加载失败: {e}"
            errors.append(detect_info["error"])
    if detect_model is not None:
        message = ("姿态权重不可用，已降级为人框检测（无关键点，精度下降）；"
                   "物品检测复用同一权重")
        status = _make_status("fallback", pose_info, detect_info,
                              True, message)
        return Detector(detect_model, detect_model, pose_mode=False,
                        conf=conf, status=status)

    # 3) 完全降级：空检测器，流水线继续运行
    message = ("检测权重全部不可用，已进入降级停用模式（推理返回空结果，"
               "服务不中断）：" + "；".join(errors))
    logger.error(message)
    return DegradedDetector(message, pose_info, detect_info)
