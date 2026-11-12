"""座位预约数据存储 —— reservations.json（演示用本地存储）

比赛演示口径: 预约系统对接接口已预留（本模块即数据源适配层），
实际部署时替换为对接学校座位预约系统的 API/数据库。

预约记录: {"id", "seat_id", "card_no"(一卡通后4位), "start"("10:00"), "end"("12:00"), "created_at"}
用途: "预约-实到"偏差检测 ——
  - 座位在预约时段内应有人: 人在=正常履约; 人长期不在=爽约/空占资源
  - 座位无预约记录却长期被物品占据: 强化"疑似占座"判定置信度
"""
import json
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional


class ReservationStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._items: List[dict] = []
        self.reload()

    def reload(self):
        if self.path.exists():
            with open(self.path, "r", encoding="utf-8") as f:
                self._items = json.load(f)
        else:
            self._items = []

    def _save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._items, f, ensure_ascii=False, indent=2)

    def all(self) -> List[dict]:
        with self._lock:
            return [dict(r) for r in self._items]

    def add(self, seat_id: str, card_no: str, start: str, end: str) -> dict:
        rec = {
            "id": uuid.uuid4().hex[:8],
            "seat_id": seat_id,
            "card_no": card_no[-4:],   # 只存后4位, 最小化隐私数据
            "start": start,
            "end": end,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        with self._lock:
            self._items.append(rec)
            self._save()
        return rec

    def remove(self, rid: str) -> bool:
        with self._lock:
            before = len(self._items)
            self._items = [r for r in self._items if r["id"] != rid]
            changed = len(self._items) != before
            if changed:
                self._save()
            return changed

    def active_for(self, seat_id: str, now: Optional[datetime] = None) -> Optional[dict]:
        """查询某座位当前时刻是否在预约时段内"""
        now = now or datetime.now()
        cur = now.hour * 60 + now.minute
        with self._lock:
            for r in self._items:
                if r["seat_id"] != seat_id:
                    continue
                try:
                    sh, sm = map(int, r["start"].split(":"))
                    eh, em = map(int, r["end"].split(":"))
                except (ValueError, AttributeError):
                    continue
                if sh * 60 + sm <= cur <= eh * 60 + em:
                    return dict(r)
        return None
