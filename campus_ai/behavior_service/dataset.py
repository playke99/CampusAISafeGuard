"""行为规范数据集工具：YOLO 标注校验、划分与 data.yaml 生成。

标注目录约定：
  dataset/
    images/*.jpg|bmp|png
    labels/*.txt   （每行：class_id cx cy w h，坐标 0-1 归一化）
"""
from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass, field

IMG_EXTS = (".jpg", ".jpeg", ".png", ".bmp")


@dataclass
class DatasetReport:
    total_images: int = 0
    labeled_images: int = 0
    total_boxes: int = 0
    class_hist: dict[int, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_label_file(path: str, num_classes: int) -> tuple[int, list[str]]:
    errors: list[str] = []
    boxes = 0
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) != 5:
                errors.append(f"{os.path.basename(path)}:{lineno} 列数不为5")
                continue
            try:
                class_id = int(parts[0])
                coords = [float(x) for x in parts[1:]]
            except ValueError:
                errors.append(f"{os.path.basename(path)}:{lineno} 含非数值")
                continue
            if not (0 <= class_id < num_classes):
                errors.append(f"{os.path.basename(path)}:{lineno} 类别ID {class_id} 越界")
            if any(not (0.0 <= c <= 1.0) for c in coords) or coords[2] <= 0 or coords[3] <= 0:
                errors.append(f"{os.path.basename(path)}:{lineno} 坐标/宽高非法")
            else:
                boxes += 1
    return boxes, errors


def validate_dataset(root: str, classes: list[str]) -> DatasetReport:
    report = DatasetReport()
    img_dir = os.path.join(root, "images")
    lbl_dir = os.path.join(root, "labels")
    if not os.path.isdir(img_dir):
        report.errors.append(f"缺少图像目录：{img_dir}")
        return report
    if not os.path.isdir(lbl_dir):
        report.errors.append(f"缺少标注目录：{lbl_dir}")
        return report

    images = [f for f in sorted(os.listdir(img_dir)) if f.lower().endswith(IMG_EXTS)]
    report.total_images = len(images)
    for image in images:
        stem = os.path.splitext(image)[0]
        label_path = os.path.join(lbl_dir, stem + ".txt")
        if not os.path.exists(label_path):
            report.errors.append(f"{image} 缺少对应标注文件")
            continue
        boxes, errors = validate_label_file(label_path, len(classes))
        report.errors.extend(errors)
        if not errors:
            report.labeled_images += 1
            report.total_boxes += boxes
            with open(label_path, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        class_id = int(line.split()[0])
                        report.class_hist[class_id] = report.class_hist.get(class_id, 0) + 1
    return report


def split_dataset(root: str, val_ratio: float = 0.2, seed: int = 42) -> dict[str, list[str]]:
    img_dir = os.path.join(root, "images")
    images = [f for f in sorted(os.listdir(img_dir)) if f.lower().endswith(IMG_EXTS)]
    rng = random.Random(seed)
    rng.shuffle(images)
    n_val = max(1, int(len(images) * val_ratio)) if images else 0
    split = {"val": images[:n_val], "train": images[n_val:]}
    with open(os.path.join(root, "split.json"), "w", encoding="utf-8") as f:
        json.dump(split, f, ensure_ascii=False, indent=2)
    return split


def write_data_yaml(root: str, classes: list[str]) -> str:
    """生成 YOLO 训练用 data.yaml（JSON 语法是 YAML 子集，ultralytics 可直接读取）。"""
    content = {
        "path": os.path.abspath(root),
        "train": "images",
        "val": "images",
        "names": {i: name for i, name in enumerate(classes)},
    }
    out = os.path.join(root, "data.yaml")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(content, f, ensure_ascii=False, indent=2)
    return out
