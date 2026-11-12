"""座位状态机 —— 占座判定的核心逻辑

每个座位独立维护状态:
  FREE      空位(无人, 或无物品)
  OCCUPIED  人在座
  AWAY      人暂离(物品在, 缺席 < 宽限期) —— 去"取书/上厕所"场景, 不计嫌疑
  TIMING    计时中(物品在, 宽限期 < 缺席 < 阈值)
  SUSPECT   疑似占座(物品在, 缺席 >= 阈值, 已生成事件)

防反复报:
  - SUSPECT 状态下不重复生成事件, 只更新持续时长
  - 当日该座位已产生过事件并处理完毕后再次触发 -> 新事件, repeat_count+1
  - 人回到座位: 记录回座时刻; 若事件仍在"待处理", 自动标记 auto_resolved(贴提示条后回座场景)

人不在的确认(防抖): 连续 absent_confirm_frames 次检测到"ROI 内无上半身关键点"
才算真离开, 单帧漏检/遮挡不误判。
"""
import json
import logging
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

from .detector import Detector, Item, Person

logger = logging.getLogger(__name__)


class SeatStatus(str, Enum):
    FREE = "free"
    OCCUPIED = "occupied"
    AWAY = "away"
    TIMING = "timing"
    SUSPECT = "suspect"


STATUS_CN = {
    SeatStatus.FREE: "空位",
    SeatStatus.OCCUPIED: "在座",
    SeatStatus.AWAY: "暂离",
    SeatStatus.TIMING: "离开计时中",
    SeatStatus.SUSPECT: "疑似占座",
}


def _point_in_polygon(x: float, y: float, polygon_norm, w: int, h: int) -> bool:
    """射线法判断点是否落在归一化多边形内（纯 Python，不依赖 cv2）"""
    pts = [(px * w, py * h) for px, py in polygon_norm]
    if len(pts) < 3:
        return False
    inside = False
    j = len(pts) - 1
    for i in range(len(pts)):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if ((yi > y) != (yj > y)) and \
                (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


class SeatRuntime:
    """单个座位的运行时状态"""

    def __init__(self, seat_id: str, zone_name: str, polygon):
        self.seat_id = seat_id
        self.zone_name = zone_name
        self.polygon = polygon
        self.status = SeatStatus.FREE
        self.person_miss = 0          # 连续未检出人的次数
        self.absent_since: Optional[float] = None  # 确认"人不在"的起始时刻
        self.items: List[str] = []
        self.item_since: Optional[float] = None    # 物品首次出现在座位的时刻
        self.suspect_event: Optional[dict] = None  # 当前未处理事件
        self.events_today: int = 0
        self.today: str = ""
        self.last_bg_ratio: float = 0.0    # 最近一次背景前景占比
        self.bg_static: bool = False       # 背景建模判定"静态遗留物"

    # ── 状态快照(API 用) ─────────────────────────────────
    def snapshot(self, now: float) -> dict:
        absent_min = 0.0
        if self.absent_since:
            absent_min = round((now - self.absent_since) / 60.0, 1)
        item_dwell_min = 0.0
        if self.item_since:
            item_dwell_min = round((now - self.item_since) / 60.0, 1)
        return {
            "seat_id": self.seat_id,
            "zone_name": self.zone_name,
            "status": self.status.value,
            "status_cn": STATUS_CN[self.status],
            "items": list(self.items),
            "absent_minutes": absent_min if self.status in (SeatStatus.TIMING, SeatStatus.AWAY, SeatStatus.SUSPECT) else 0.0,
            "item_dwell_minutes": item_dwell_min if self.items else 0.0,
            "has_open_event": self.suspect_event is not None,
            "events_today": self.events_today,
            "bg_ratio": round(self.last_bg_ratio, 3),
            "bg_static": self.bg_static,
        }


class SeatStateMachine:
    """管理全部座位的状态转移与事件生成"""

    def __init__(self, detector: Detector, settings, snapshot_dir: Path,
                 reservation_store=None):
        self.detector = detector
        self.settings = settings
        self.reservation_store = reservation_store   # ReservationStore | None
        self.snapshot_dir = Path(snapshot_dir)
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.seats: Dict[str, SeatRuntime] = {}
        self._last_frame = None       # 最近一帧(快照/调试用)
        self._lock = None             # 主循环单线程调用, 无需锁

    # ── ROI 同步 ─────────────────────────────────────────
    def sync_rois(self, rois: List[dict]):
        for roi in rois:
            sid = roi["seat_id"]
            if sid not in self.seats:
                self.seats[sid] = SeatRuntime(sid, roi["zone_name"], roi["polygon"])
            else:
                st = self.seats[sid]
                st.polygon = roi["polygon"]
                st.zone_name = roi["zone_name"]
        for sid in list(self.seats):
            if sid not in {r["seat_id"] for r in rois}:
                del self.seats[sid]

    # ── 每次检测的主入口 ──────────────────────────────────
    def process(self, frame, persons: List[Person], items: List[Item],
                bg_signals: Optional[Dict[str, dict]] = None) -> List[dict]:
        """返回本次新生成的占座事件列表

        bg_signals: {seat_id: {"fg_ratio": float, "static": bool}}，由背景建模
        线程产出；static=True 且本座无检测物品时，补充"遗留物(背景)"证据参与计时。
        """
        now = time.time()
        self._last_frame = frame
        generated: List[dict] = []
        h, w = frame.shape[:2]
        today = time.strftime("%Y-%m-%d")
        default_threshold = float(self.settings.get("absent_threshold_minutes"))
        seat_thresholds = self.settings.get("seat_thresholds") or {}
        grace_sec = float(self.settings.get("grace_seconds"))
        need_confirm = int(self.settings.get("absent_confirm_frames"))
        require_items = bool(self.settings.get("require_items")) and self.detector.items_enabled
        bg_cfg = self.settings.get("background") or {}
        bg_assist = bool(bg_cfg.get("assist_items", True))

        # 物品归属: 框底部中心落在哪个座位 ROI
        seat_items: Dict[str, List[str]] = {sid: [] for sid in self.seats}
        for it in items:
            x1, y1, x2, y2 = it.bbox
            bx, by = (x1 + x2) / 2, y2  # 底边中心
            for st in self.seats.values():
                if _point_in_polygon(bx, by, st.polygon, w, h):
                    seat_items[st.seat_id].append(it.name)
                    break

        # 人在哪座: 上半身关键点落点(一人多座时同一人可命中多个相邻 ROI)
        for st in self.seats.values():
            present = any(
                _point_in_polygon(px, py, st.polygon, w, h)
                for p in persons for px, py in p.upper_body_points()
            )
            st.items = seat_items.get(st.seat_id, [])
            # 背景建模辅助：静态遗留物补充"物品在"证据（不影响"人"判定）
            sig = (bg_signals or {}).get(st.seat_id)
            if sig:
                st.last_bg_ratio = float(sig.get("fg_ratio", 0.0))
                st.bg_static = bool(sig.get("static"))
                if bg_assist:
                    if sig.get("static") and not st.items:
                        st.items = ["遗留物(背景)"]
                    elif not sig.get("static") and st.items == ["遗留物(背景)"]:
                        st.items = []
            if st.items and st.item_since is None:
                st.item_since = now
            elif not st.items:
                st.item_since = None

            if present:
                st.person_miss = 0
                st.absent_since = None
                if st.status in (SeatStatus.AWAY, SeatStatus.TIMING, SeatStatus.SUSPECT):
                    logger.info("座位 %s 人已回座", st.seat_id)
                    if st.suspect_event and st.suspect_event.get("status") == "待处理":
                        # 贴提示条后回座 -> 自动关单
                        st.suspect_event["status"] = "已自动关闭(人已回座)"
                        st.suspect_event["resolved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                        self._persist_event(st.suspect_event)
                        logger.info("座位 %s 事件自动关闭: 人已回座", st.seat_id)
                    st.suspect_event = None
                st.status = SeatStatus.OCCUPIED
                continue

            # ---- 以下为"本帧未检出人" ----
            st.person_miss += 1
            if st.person_miss < need_confirm:
                continue  # 短暂漏检, 保持原状态

            if st.status == SeatStatus.OCCUPIED:
                st.absent_since = now
                if st.items or not require_items:
                    st.status = SeatStatus.AWAY          # 先进暂离观察期
                else:
                    st.status = SeatStatus.FREE          # 无物品且要求物品 -> 空位
                continue

            if st.status == SeatStatus.FREE:
                if st.items or not require_items:
                    # 空位上出现遗留物品(或物品检出滞后) -> 开始计时
                    if st.absent_since is None:
                        st.absent_since = now
                    st.status = SeatStatus.TIMING
                continue

            if st.status in (SeatStatus.AWAY, SeatStatus.TIMING, SeatStatus.SUSPECT):
                if not st.items and require_items:
                    # 人不在且物品也被拿走 -> 正常离开
                    st.status = SeatStatus.FREE
                    st.absent_since = None
                    continue
                absent_sec = now - (st.absent_since or now)
                absent_min = absent_sec / 60.0
                threshold_min = float(seat_thresholds.get(st.seat_id, default_threshold))
                if st.status in (SeatStatus.AWAY, SeatStatus.TIMING):
                    if absent_sec <= grace_sec:
                        st.status = SeatStatus.AWAY
                    elif absent_min < threshold_min:
                        st.status = SeatStatus.TIMING
                    else:
                        st.status = SeatStatus.SUSPECT
                        event = self._create_event(st, absent_min, today, frame)
                        if event:
                            generated.append(event)
                elif st.status == SeatStatus.SUSPECT:
                    if st.suspect_event:
                        st.suspect_event["absent_minutes"] = round(absent_min, 1)

        # today 跨天重置
        for st in self.seats.values():
            if st.today != today:
                st.today = today
                st.events_today = 0

        return generated

    # ── 事件生成(含当日去重/二次判定) ────────────────────────
    def _create_event(self, st: SeatRuntime, absent_min: float, today: str, frame) -> Optional[dict]:
        st.events_today += 1
        repeat = st.events_today
        snap_path = ""
        if self._last_frame is not None:
            name = f"{st.seat_id}_{time.strftime('%H%M%S')}.jpg"
            try:
                import cv2
                cv2.imwrite(str(self.snapshot_dir / name), self._last_frame)
                snap_path = f"snapshots/{name}"
            except Exception as e:
                logger.warning("快照保存失败: %s", e)

        # 一人多座分析: 邻近"有人"座位与"物品在+人不在"座位空间关联
        neighbors = self._occupied_neighbors(st)
        # 预约-实到偏差: 事件时刻该座位是否在预约时段内
        reservation = None
        if self.reservation_store is not None:
            try:
                reservation = self.reservation_store.active_for(st.seat_id)
            except Exception as e:
                logger.warning("预约查询失败: %s", e)

        event = {
            "event_id": uuid.uuid4().hex[:12],
            "event_type": "seat_occupation",
            "camera_id": self.settings.get("camera_id"),
            "zone_name": st.zone_name,
            "seat_id": st.seat_id,
            "occurred_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "absent_minutes": round(absent_min, 1),
            "items": list(st.items),
            "repeat_count": repeat,
            "snapshot": snap_path,
            "item_dwell_minutes": (
                round((time.time() - st.item_since) / 60.0, 1) if st.item_since else None),
            "status": "待处理",
            "bg_ratio": round(st.last_bg_ratio, 3),
            "multi_seat": bool(neighbors),
            "multi_seat_neighbors": neighbors,
            "reservation": (
                {"card_no": reservation["card_no"], "slot": f"{reservation['start']}~{reservation['end']}"}
                if reservation else None
            ),
        }
        st.suspect_event = event
        self._persist_event(event)
        extra = ""
        if neighbors:
            extra += f", 邻座{neighbors}有人(疑似一人占多座)"
        if reservation:
            extra += f", 预约在册(尾号{reservation['card_no']})但人不在"
        logger.warning("疑似占座: %s (缺席 %.1f 分钟, 物品=%s, 当日第%d次%s)",
                       st.seat_id, absent_min, st.items, repeat, extra)
        return event

    def _occupied_neighbors(self, st: SeatRuntime) -> List[str]:
        """与 st 的 ROI 中心距离较近且当前有人的座位 —— 疑似同一人占用多座"""
        import numpy as np
        me = np.mean(st.polygon, axis=0)
        out = []
        for other in self.seats.values():
            if other.seat_id == st.seat_id or other.status != SeatStatus.OCCUPIED:
                continue
            oc = np.mean(other.polygon, axis=0)
            # 邻近阈值: 两 ROI 中心距 < 1.5 倍本座 ROI 平均边长
            size = np.abs(np.array(st.polygon) - me).mean() * 1.5
            if float(np.linalg.norm(me - oc)) < float(size):
                out.append(other.seat_id)
        return sorted(out)

    def _persist_event(self, event: dict):
        try:
            log_file = self.snapshot_dir.parent / "events.json"
            data = []
            if log_file.exists():
                with open(log_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            for i, e in enumerate(data):
                if e["event_id"] == event["event_id"]:
                    data[i] = event
                    break
            else:
                data.insert(0, event)
            with open(log_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("事件落盘失败: %s", e)

    def load_events(self) -> List[dict]:
        log_file = self.snapshot_dir.parent / "events.json"
        if log_file.exists():
            with open(log_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return []

    def resolve_event(self, event_id: str, action: str, note: str = "") -> bool:
        """管理员处置: action ∈ 贴提示条 / 清座 / 误报"""
        for st in self.seats.values():
            ev = st.suspect_event
            if ev and ev["event_id"] == event_id:
                ev["status"] = f"已处理({action})"
                ev["resolved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                ev["note"] = note
                self._persist_event(ev)
                st.suspect_event = None
                st.status = SeatStatus.FREE
                st.absent_since = None
                return True
        events = self.load_events()
        for e in events:
            if e["event_id"] == event_id:
                e["status"] = f"已处理({action})"
                e["resolved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                e["note"] = note
                self._persist_event(e)
                return True
        return False

    def all_states(self) -> List[dict]:
        now = time.time()
        return [st.snapshot(now) for st in self.seats.values()]
