"""行为规范模型训练入口。

- 有 ultralytics 且非 dry-run：执行真实训练。
- 无 GPU/无依赖：--dry-run 完成数据集校验、划分与训练清单输出，流程可验证。
"""
from __future__ import annotations

import argparse
import json

from .dataset import split_dataset, validate_dataset, write_data_yaml


def train(data_root: str, classes: list[str], epochs: int = 100,
          model: str = "yolo11n.pt", dry_run: bool = False) -> dict:
    report: dict = {}
    check = validate_dataset(data_root, classes)
    report["dataset_ok"] = check.ok
    report["dataset"] = {
        "total_images": check.total_images,
        "labeled_images": check.labeled_images,
        "total_boxes": check.total_boxes,
        "class_hist": check.class_hist,
        "errors": check.errors[:10],
    }
    if not check.ok:
        report["status"] = "dataset_invalid"
        return report

    split = split_dataset(data_root)
    yaml_path = write_data_yaml(data_root, classes)
    report["split"] = {k: len(v) for k, v in split.items()}
    report["data_yaml"] = yaml_path

    if dry_run:
        report["status"] = "dry_run_ok"
        return report

    try:
        from ultralytics import YOLO  # type: ignore
    except ImportError:
        report["status"] = "skipped"
        report["reason"] = "ultralytics 未安装，请在 GPU 训练环境执行"
        return report

    trainer = YOLO(model)
    result = trainer.train(data=yaml_path, epochs=epochs, imgsz=640)
    report["status"] = "trained"
    report["save_dir"] = str(getattr(result, "save_dir", ""))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="行为规范识别模型训练")
    parser.add_argument("--data", required=True, help="数据集根目录")
    parser.add_argument("--classes", default="slippers,breakfast", help="类别，逗号分隔")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--model", default="yolo11n.pt")
    parser.add_argument("--dry-run", action="store_true", help="仅校验数据集与流程")
    args = parser.parse_args()
    result = train(args.data, [c.strip() for c in args.classes.split(",")],
                   epochs=args.epochs, model=args.model, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
