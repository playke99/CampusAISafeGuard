"""视觉事件判定（纯算法，零三方依赖，可独立单测）。

COCO 17 关键点索引：
0鼻 1左眼 2右眼 3左耳 4右耳 5左肩 6右肩 7左肘 8右肘
9左腕 10右腕 11左髋 12右髋 13左膝 14右膝 15左踝 16右踝

判定规则：
- 摔倒：躯干倾斜角 > 65°（肩中点→髋中点与竖直向下方向夹角）
- 攻击：滑窗内手腕位移 Z-Score > 3.0
- 围堵：任意两人间距 < 3.5 × 肩宽单位距离
"""
from __future__ import annotations

import math

L_SHOULDER, R_SHOULDER = 5, 6
L_WRIST, R_WRIST = 9, 10
L_HIP, R_HIP = 11, 12
KEYPOINT_COUNT = 17


def _mid(kps: list[list[float]], a: int, b: int) -> tuple[float, float]:
    return ((kps[a][0] + kps[b][0]) / 2, (kps[a][1] + kps[b][1]) / 2)


def valid_person(kps: list[list[float]] | None) -> bool:
    """关键点完整且非全零，才可参与判定。"""
    if not kps or len(kps) < KEYPOINT_COUNT:
        return False
    try:
        return any(abs(p[0]) + abs(p[1]) > 1e-6 for p in kps)
    except (TypeError, IndexError):
        return False


def torso_tilt_angle(kps: list[list[float]]) -> float:
    """躯干倾斜角：站立≈0°，平躺≈90°。"""
    sx, sy = _mid(kps, L_SHOULDER, R_SHOULDER)
    hx, hy = _mid(kps, L_HIP, R_HIP)
    vx, vy = hx - sx, hy - sy
    norm = math.hypot(vx, vy)
    if norm < 1e-6:
        return 0.0
    cos = max(-1.0, min(1.0, vy / norm))
    return math.degrees(math.acos(cos))


def wrist_displacements(frames: list[list[list[float]]]) -> list[float]:
    """逐帧计算双手腕位移幅度（相邻帧欧氏距离之和）。"""
    out: list[float] = []
    for prev, cur in zip(frames, frames[1:]):
        d = math.dist(prev[L_WRIST][:2], cur[L_WRIST][:2]) + \
            math.dist(prev[R_WRIST][:2], cur[R_WRIST][:2])
        out.append(d)
    return out


def z_score(series: list[float], value: float) -> float:
    if len(series) < 2:
        return 0.0
    mean = sum(series) / len(series)
    var = sum((x - mean) ** 2 for x in series) / len(series)
    std = math.sqrt(var)
    if std < 1e-9:
        return 0.0
    return (value - mean) / std


def shoulder_unit(kps: list[list[float]]) -> float:
    return math.dist(kps[L_SHOULDER][:2], kps[R_SHOULDER][:2])


def person_distance(a: list[list[float]], b: list[list[float]]) -> float:
    ax, ay = _mid(a, L_SHOULDER, R_SHOULDER)
    bx, by = _mid(b, L_SHOULDER, R_SHOULDER)
    return math.dist((ax, ay), (bx, by))


def detect_fall(kps: list[list[float]], threshold_deg: float) -> dict | None:
    angle = torso_tilt_angle(kps)
    if angle > threshold_deg:
        return {"tilt_angle": round(angle, 2), "threshold": threshold_deg}
    return None


def detect_attack(frames: list[list[list[float]]],
                  z_threshold: float) -> dict | None:
    disps = wrist_displacements(frames)
    if len(disps) < 2:
        return None
    z = z_score(disps[:-1], disps[-1])
    if z > z_threshold:
        return {"z_score": round(z, 2), "threshold": z_threshold,
                "latest_displacement": round(disps[-1], 2)}
    return None


def detect_surround(persons_kps: list[list[list[float]]],
                    ratio: float) -> dict | None:
    if len(persons_kps) < 2:
        return None
    best: dict | None = None
    for i in range(len(persons_kps)):
        for j in range(i + 1, len(persons_kps)):
            unit = (shoulder_unit(persons_kps[i]) +
                    shoulder_unit(persons_kps[j])) / 2
            if unit < 1e-6:
                continue
            d = person_distance(persons_kps[i], persons_kps[j])
            if d < ratio * unit and (best is None or d < best["distance"]):
                best = {"distance": round(d, 2),
                        "reference": round(ratio * unit, 2),
                        "person_pair": [i, j], "ratio": ratio}
    return best
