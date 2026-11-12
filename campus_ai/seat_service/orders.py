"""霸座工单生成：seat_monitor 疑似占座事件 → 图书管理员待处理工单。

- 工单级别：当日首次黄级、重复触发橙级。
- 工单内容：座位、区域、滞留时长、遗留物品、快照、预约偏差、一人多座邻座。
"""
from __future__ import annotations

from typing import Any

from campus_ai.common.events import BaseEvent, EventType, Severity, make_event


def work_order_from_event(payload: dict[str, Any], location: str,
                          targets: list[str]) -> BaseEvent:
    seat_id = str(payload.get("seat_id") or "unknown")
    repeat = int(payload.get("repeat_count") or 1)
    severity = Severity.ORANGE if repeat >= 2 else Severity.YELLOW
    absent = round(float(payload.get("absent_minutes") or 0), 1)
    items = list(payload.get("items") or [])
    reservation = payload.get("reservation")
    neighbors = list(payload.get("multi_seat_neighbors") or [])

    extra = ""
    if neighbors:
        extra += f"，邻座 {'/'.join(neighbors)} 有人（疑似一人占多座）"
    if reservation:
        extra += f"，预约在册（尾号 {reservation.get('card_no')}）但人不在"
    summary = (f"疑似霸座：座位 {seat_id} 物品滞留 {absent} 分钟"
               f"（当日第 {repeat} 次）{extra}")
    event = make_event(
        EventType.WORK_ORDER, severity,
        source=str(payload.get("camera_id") or "lib-cam-01"),
        location=f"{str(payload.get('zone_name') or location)} / {seat_id}",
        summary=summary,
        detail={
            "seat_id": seat_id,
            "absent_minutes": absent,
            "items": items,
            "snapshot": payload.get("snapshot", ""),
            "reservation": reservation,
            "repeat_count": repeat,
            "multi_seat_neighbors": neighbors,
            "status": "待处理",
            "occurred_at": payload.get("occurred_at", ""),
            "item_dwell_minutes": payload.get("item_dwell_minutes"),
        },
        targets=list(targets),
    )
    return event
