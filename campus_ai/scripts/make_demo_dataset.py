"""生成行为规范演示数据集（合成 BMP + 合法 YOLO 标注）。

用途：离线验证「数据校验 → 划分 → 训练 dry-run」链路，不依赖真实采集合规数据。
"""
from __future__ import annotations

import os
import struct

CLASSES = ["slippers", "breakfast"]
_SIZE = (160, 120)
_COLORS = {
    "slippers": (210, 120, 40),
    "breakfast": (40, 160, 210),
}


def _write_bmp(path: str, width: int, height: int, rgb: tuple[int, int, int]) -> None:
    """写入 24bit BMP（零依赖）。"""
    row_size = (width * 3 + 3) & ~3
    pixel_bytes = row_size * height
    file_size = 14 + 40 + pixel_bytes
    b, g, r = rgb[2], rgb[1], rgb[0]          # BMP 像素顺序为 BGR
    row = (bytes((b, g, r)) * width) + b"\x00" * (row_size - width * 3)
    with open(path, "wb") as f:
        f.write(b"BM")
        f.write(struct.pack("<IHHI", file_size, 0, 0, 14 + 40))
        f.write(struct.pack("<IiiHHIIiiII", 40, width, height, 1, 24, 0,
                            pixel_bytes, 2835, 2835, 0, 0))
        for _ in range(height):
            f.write(row)


def make_dataset(root: str, per_class: int = 5) -> str:
    """生成 images/*.bmp 与 labels/*.txt，返回数据集根目录。"""
    img_dir = os.path.join(root, "images")
    lbl_dir = os.path.join(root, "labels")
    os.makedirs(img_dir, exist_ok=True)
    os.makedirs(lbl_dir, exist_ok=True)
    for class_id, name in enumerate(CLASSES):
        for idx in range(per_class):
            stem = f"{name}_{idx:03d}"
            _write_bmp(os.path.join(img_dir, stem + ".bmp"),
                       *_SIZE, _COLORS.get(name, (120, 120, 120)))
            with open(os.path.join(lbl_dir, stem + ".txt"), "w",
                      encoding="utf-8") as f:
                f.write(f"{class_id} 0.5 0.5 0.4 0.5\n")
    return root
