"""座位 ROI 标定存储 —— rois.json

每个座位是一个多边形(归一化坐标 0~1, 与分辨率解耦):
{"seat_id": "A01", "zone_name": "图书馆自习区A01", "polygon": [[x,y], ...]}
"""
import json
import threading
import uuid
from pathlib import Path
from typing import List, Optional


class RoiStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._seats: List[dict] = []
        self.reload()

    def reload(self):
        if self.path.exists():
            with open(self.path, "r", encoding="utf-8") as f:
                self._seats = json.load(f)
        else:
            self._seats = []

    def _save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._seats, f, ensure_ascii=False, indent=2)

    def all(self) -> List[dict]:
        with self._lock:
            return [dict(s) for s in self._seats]

    def replace_all(self, seats: List[dict]) -> List[dict]:
        """整体覆盖标定(前端画完一次提交)"""
        cleaned = []
        for s in seats:
            poly = [[round(float(x), 4), round(float(y), 4)] for x, y in s["polygon"]]
            if len(poly) < 3:
                continue
            cleaned.append({
                "seat_id": str(s.get("seat_id") or f"S{uuid.uuid4().hex[:4]}"),
                "zone_name": str(s.get("zone_name") or f"座位 {s.get('seat_id', '')}"),
                "polygon": poly,
            })
        with self._lock:
            self._seats = cleaned
            self._save()
            # 注意：不能在持锁时调用 self.all()（Lock 不可重入，会死锁）
            return [dict(s) for s in self._seats]

    def find(self, seat_id: str) -> Optional[dict]:
        with self._lock:
            for s in self._seats:
                if s["seat_id"] == seat_id:
                    return dict(s)
        return None
