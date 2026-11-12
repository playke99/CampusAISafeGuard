"""姿态演示数据构造器：生成站立/摔倒/攻击/围堵的 COCO-17 关键点序列。

仅用于离线演示与自动化测试，不参与生产推理。
画布坐标 640×480，像素坐标，y 轴向下。
"""
from __future__ import annotations

import math
import random
from typing import Any

CENTER_X, CENTER_Y = 320, 150


def standing(cx: float = CENTER_X, cy: float = CENTER_Y,
             scale: float = 1.0) -> list[list[float]]:
    s = scale
    return [
        [cx, cy - 30 * s], [cx - 6 * s, cy - 32 * s], [cx + 6 * s, cy - 32 * s],
        [cx - 12 * s, cy - 30 * s], [cx + 12 * s, cy - 30 * s],
        [cx - 16 * s, cy], [cx + 16 * s, cy],
        [cx - 26 * s, cy + 35 * s], [cx + 26 * s, cy + 35 * s],
        [cx - 26 * s, cy + 68 * s], [cx + 26 * s, cy + 68 * s],
        [cx - 12 * s, cy + 70 * s], [cx + 12 * s, cy + 70 * s],
        [cx - 12 * s, cy + 140 * s], [cx + 12 * s, cy + 140 * s],
        [cx - 12 * s, cy + 205 * s], [cx + 12 * s, cy + 205 * s],
    ]


def rotate(points: list[list[float]], cx: float, cy: float,
           degrees: float) -> list[list[float]]:
    rad = math.radians(degrees)
    cos, sin = math.cos(rad), math.sin(rad)
    out = []
    for x, y in points:
        dx, dy = x - cx, y - cy
        out.append([round(cx + dx * cos - dy * sin, 2),
                    round(cy + dx * sin + dy * cos, 2)])
    return out


def fallen(cx: float = CENTER_X, cy: float = CENTER_Y,
           scale: float = 1.0) -> list[list[float]]:
    """躯干旋转 90°，倾斜角约 90° > 65° 阈值。"""
    return rotate(standing(cx, cy, scale), cx, cy, 90)


def _jitter(points: list[list[float]], amplitude: float = 1.5,
            seed: int | None = None) -> list[list[float]]:
    rng = random.Random(seed)
    return [[x + rng.uniform(-amplitude, amplitude),
             y + rng.uniform(-amplitude, amplitude)] for x, y in points]


def attack_window(frames: int = 20, cx: float = CENTER_X,
                  cy: float = CENTER_Y) -> list[list[list[list[float]]]]:
    """单人攻击：前 19 帧手部轻微抖动，末帧手腕大幅挥动。"""
    window = []
    for i in range(frames - 1):
        window.append([_jitter(standing(cx, cy), 1.2, seed=i)])
    strike = standing(cx, cy)
    strike[9] = [cx - 150, cy - 40]     # 左腕
    strike[10] = [cx + 150, cy - 40]    # 右腕
    strike[7] = [cx - 90, cy + 10]      # 左肘
    strike[8] = [cx + 90, cy + 10]      # 右肘
    window.append([strike])
    return window


def surround_frame(cx: float = CENTER_X, cy: float = CENTER_Y,
                   count: int = 4) -> list[list[list[float]]]:
    """多人围堵：4 人环绕，任意两人间距均小于 3.5 倍肩宽。"""
    offsets = [(0, 0), (70, 0), (-70, 0), (0, 70), (0, -50)][:max(2, count)]
    return [[standing(cx + ox, cy + oy, 0.9) for ox, oy in offsets]]


def bullying_window(frames: int = 20, cx: float = CENTER_X,
                    cy: float = CENTER_Y) -> list[list[list[list[float]]]]:
    """围堵 + 攻击：4 人贴近，其中 1 人末帧挥拳。"""
    base = surround_frame(cx, cy)
    window = []
    for i in range(frames - 1):
        window.append([_jitter(person, 1.0, seed=i * 10 + j)
                       for j, person in enumerate(base[0])])
    last = [list(person) for person in base[0]]
    last[0][9] = [cx - 150, cy - 40]
    last[0][10] = [cx + 150, cy - 40]
    last[0][7] = [cx - 90, cy + 10]
    last[0][8] = [cx + 90, cy + 10]
    window.append(last)
    return window


SCENES: dict[str, dict[str, Any]] = {
    "attack": {"camera_id": "dorm-cam-02", "location": "宿舍楼B座2层楼道",
               "title": "单人暴力攻击", "frames": "attack_window"},
    "fall": {"camera_id": "play-cam-01", "location": "操场东南角",
             "title": "人员意外摔倒", "frames": "fall_frame"},
    "surround": {"camera_id": "teach-cam-02", "location": "教学楼A栋2层走廊",
                 "title": "多人围堵", "frames": "surround_frame"},
    "bullying": {"camera_id": "dorm-cam-02", "location": "宿舍楼B座2层楼道",
                 "title": "霸凌冲突（围堵+攻击）", "frames": "bullying_window"},
}


def build(name: str) -> tuple[list, str, str, str]:
    """返回 (frames, camera_id, location, title)。"""
    meta = SCENES[name]
    if name == "attack":
        frames = attack_window()
    elif name == "fall":
        frames = [[fallen()]]
    elif name == "surround":
        frames = surround_frame()
    else:
        frames = bullying_window()
    return frames, meta["camera_id"], meta["location"], meta["title"]
