"""Agent(8100) 推送客户端 —— 占座事件走 Agent 处置管线

链路: seat_monitor 事件 → POST /api/seat_event → RAG(图书馆占座管理办法)
     → LLM 处置报告 → 规则派单(图书馆值班馆员) → 推 Spring 工单(48080)

推送失败不重试不阻塞检测循环 —— 事件已在本地 events.json 落盘,
Agent 恢复后管理员仍可从前端工单页看到（Agent 本地 /api/workorders 兜底）。
"""
import logging

import requests

logger = logging.getLogger(__name__)


def push_seat_event(event: dict, agent_url: str,
                    agent_token: str = "") -> bool:
    """把疑似占座事件推给 Agent 处置; 当日 repeat_count>=2 升级为中危"""
    payload = {
        "seat_id": event["seat_id"],
        "zone_name": event["zone_name"],
        "camera_id": event.get("camera_id", "lib-cam-01"),
        "occurred_at": event["occurred_at"],
        "absent_minutes": event["absent_minutes"],
        "items": event.get("items", []),
        "repeat_count": event.get("repeat_count", 1),
        "snapshot": event.get("snapshot", ""),
        "item_dwell_minutes": event.get("item_dwell_minutes"),
        "multi_seat_neighbors": event.get("multi_seat_neighbors", []),
        "reservation": event.get("reservation"),
        "severity": "中危" if event.get("repeat_count", 1) >= 2 else "低危",
    }
    url = agent_url.rstrip("/") + "/api/seat_event"
    headers = {"X-Integration-Token": agent_token} if agent_token else {}
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        orders = [o["order_id"] for o in data.get("orders", [])]
        logger.info("占座事件 %s 已推送 Agent: 派单 %d 张%s",
                    event["event_id"], len(orders),
                    "（降级模式）" if data.get("degraded") else "")
        return True
    except Exception as e:
        logger.warning("占座事件 %s 推送 Agent 失败（本地已落盘）: %s",
                       event["event_id"], e)
        return False
