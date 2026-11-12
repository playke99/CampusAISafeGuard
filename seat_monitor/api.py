"""FastAPI 接口 —— 前端(3000)直连, CORS 全开

GET  /api/state                 全部座位实时状态
GET  /api/rois                  ROI 标定读取
POST /api/rois                  ROI 标定保存(整体覆盖)
GET  /api/snapshot.jpg?annotated=1   当前帧快照
GET  /api/video_feed            MJPEG 视频流
GET  /api/events                占座事件列表(支持 seat_id/status/q/date 过滤)
POST /api/events/{id}/resolve   处置: 贴提示条 / 清座 / 误报
GET  /api/settings              配置读取
POST /api/settings              阈值在线修改(如 absent_threshold_minutes=25)
GET  /api/seat_thresholds       霸座判定阈值(全局 + 按座位)
POST /api/seat_thresholds       按座位自定义阈值/清除覆盖
GET  /api/background            背景建模状态(后端/帧数/每座位前景占比)
"""
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

if TYPE_CHECKING:
    from .main import MonitorContext

logger = logging.getLogger(__name__)

# seat_monitor 独立演示页（与主系统 web/ 看板隔离，互不影响）
WEB_DIR = Path(__file__).resolve().parent / "web"


def create_app(ctx: "MonitorContext | Any") -> FastAPI:
    app = FastAPI(title="图书馆占座监测 API", version="0.3.0")
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    )

    @app.get("/api/state")
    def state():
        detector_status = getattr(ctx.detector, "status", {}) or {}
        pipeline = getattr(ctx, "pipeline", None)
        return {
            "camera_id": ctx.settings.get("camera_id"),
            "zone_prefix": ctx.settings.get("zone_prefix"),
            "source": ctx.settings.get("source"),
            "items_enabled": ctx.detector.items_enabled,
            "threshold_minutes": ctx.settings.get("absent_threshold_minutes"),
            "seat_thresholds": ctx.settings.get("seat_thresholds") or {},
            "grace_seconds": ctx.settings.get("grace_seconds"),
            "seats": ctx.machine.all_states(),
            "detector": detector_status,
            "degraded": bool(detector_status.get("degraded")),
            "degraded_message": detector_status.get("message", ""),
            "pipeline_running": (pipeline.health()["running"] if pipeline else {}),
            "server_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    @app.get("/api/health")
    def health():
        pipeline = getattr(ctx, "pipeline", None)
        if pipeline is not None:
            data = pipeline.health()
        else:
            detector_status = getattr(ctx.detector, "status", {}) or {}
            data = {"running": {}, "detector": detector_status,
                    "degraded": bool(detector_status.get("degraded")),
                    "degraded_message": detector_status.get("message", ""),
                    "server_time": time.strftime("%Y-%m-%d %H:%M:%S")}
        data["camera_id"] = ctx.settings.get("camera_id")
        data["seats"] = len(ctx.machine.all_states())
        return data

    @app.get("/api/rois")
    def get_rois():
        return ctx.roi_store.all()

    @app.post("/api/rois")
    def save_rois(seats: list[dict]):
        saved = ctx.roi_store.replace_all(seats)
        ctx.on_rois_changed()
        logger.info("ROI 标定已更新: %d 个座位", len(saved))
        return saved

    @app.get("/api/snapshot.jpg")
    def snapshot(annotated: int = Query(1)):
        frame = ctx.get_frame(bool(annotated))
        if frame is None:
            raise HTTPException(503, "暂无视频帧")
        import cv2
        ok, buf = cv2.imencode(".jpg", frame)
        if not ok:
            raise HTTPException(500, "编码失败")
        return _jpeg_response(buf)

    @app.get("/api/video_feed")
    def video_feed(annotated: int = Query(1)):
        def gen():
            import time as _t
            while True:
                frame = ctx.get_frame(bool(annotated))
                if frame is not None:
                    import cv2
                    ok, buf = cv2.imencode(".jpg", frame)
                    if ok:
                        yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                               + buf.tobytes() + b"\r\n")
                _t.sleep(0.05)
        return StreamingResponse(gen(),
                                 media_type="multipart/x-mixed-replace; boundary=frame")

    @app.get("/api/events")
    def events(limit: int = Query(100, le=500), offset: int = Query(0),
               seat_id: str = "", status: str = "", q: str = "",
               date: str = ""):
        """历史识别记录查询：按座位/状态/关键词/日期过滤"""
        records = ctx.machine.load_events()
        if seat_id:
            records = [e for e in records if e.get("seat_id") == seat_id]
        if status:
            if status == "待处理":
                records = [e for e in records if e.get("status") == "待处理"]
            elif status == "已处理":
                records = [e for e in records if e.get("status") != "待处理"]
            else:
                records = [e for e in records if status in str(e.get("status", ""))]
        if q:
            records = [e for e in records
                       if q in str(e.get("zone_name", ""))
                       or q in str(e.get("seat_id", ""))
                       or q in ",".join(map(str, e.get("items", [])))]
        if date:
            records = [e for e in records
                       if str(e.get("occurred_at", "")).startswith(date)]
        return {"records": records[offset:offset + limit], "total": len(records)}

    @app.get("/api/background")
    def background_status():
        bg = getattr(ctx, "background", None)
        data = bg.stats() if bg is not None else {"enabled": False, "backend": "none"}
        data["seats"] = {
            s["seat_id"]: {"fg_ratio": s.get("bg_ratio", 0.0),
                           "static": s.get("bg_static", False)}
            for s in ctx.machine.all_states()
        }
        return data

    @app.post("/api/events/{event_id}/resolve")
    def resolve(event_id: str, body: dict):
        action = body.get("action", "贴提示条")
        if action not in ("贴提示条", "清座", "误报"):
            raise HTTPException(400, "action 须为 贴提示条/清座/误报")
        if not ctx.machine.resolve_event(event_id, action, body.get("note", "")):
            raise HTTPException(404, "事件不存在")
        return {"ok": True, "event_id": event_id, "action": action}

    @app.get("/api/settings")
    def get_settings():
        return ctx.settings.all()

    # ── 霸座阈值：全局 + 按座位自定义 ────────────────────
    @app.get("/api/seat_thresholds")
    def get_seat_thresholds():
        return {"default_minutes": ctx.settings.get("absent_threshold_minutes"),
                "seats": ctx.settings.get("seat_thresholds") or {}}

    @app.post("/api/seat_thresholds")
    def set_seat_threshold(body: dict):
        seat_id = str(body.get("seat_id") or "").strip()
        if not seat_id:
            raise HTTPException(400, "seat_id 必填")
        thresholds = dict(ctx.settings.get("seat_thresholds") or {})
        minutes = body.get("minutes")
        if minutes is None:
            thresholds.pop(seat_id, None)
        else:
            try:
                value = float(minutes)
            except (TypeError, ValueError):
                raise HTTPException(400, "minutes 须为数字")
            if value <= 0:
                raise HTTPException(400, "minutes 须大于 0")
            thresholds[seat_id] = value
        ctx.settings.update({"seat_thresholds": thresholds})
        logger.info("座位 %s 霸座阈值已更新: %s", seat_id, thresholds.get(seat_id, "恢复全局"))
        return {"default_minutes": ctx.settings.get("absent_threshold_minutes"),
                "seats": thresholds}

    # ── 预约管理（"预约-实到"偏差检测数据源） ──────────────
    @app.get("/api/reservations")
    def get_reservations():
        return ctx.reservations.all()

    @app.post("/api/reservations")
    def add_reservation(body: dict):
        seat_id = body.get("seat_id")
        card_no = str(body.get("card_no", ""))
        start, end = body.get("start", ""), body.get("end", "")
        if not seat_id or not card_no or not start or not end:
            raise HTTPException(400, "seat_id / card_no / start / end 均必填")
        rec = ctx.reservations.add(seat_id, card_no, start, end)
        logger.info("新增预约: %s %s~%s 尾号%s", seat_id, start, end, rec["card_no"])
        return rec

    @app.delete("/api/reservations/{rid}")
    def delete_reservation(rid: str):
        if not ctx.reservations.remove(rid):
            raise HTTPException(404, "预约记录不存在")
        return {"ok": True}

    @app.post("/api/settings")
    def patch_settings(patch: dict):
        data = ctx.settings.update(patch)
        logger.info("配置已更新: %s", {k: v for k, v in patch.items() if k in data})
        return data

    # ── 静态资源：事件快照 + 单文件前端 ────────────────────
    try:
        snapshot_dir = Path(ctx.settings.path.parent) / "snapshots"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        app.mount("/snapshots", StaticFiles(directory=str(snapshot_dir)),
                  name="snapshots")
    except Exception as e:
        logger.warning("快照目录挂载失败: %s", e)

    if (WEB_DIR / "index.html").exists():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    else:
        logger.warning("前端页面不存在: %s", WEB_DIR / "index.html")

    return app


def _jpeg_response(buf):
    from fastapi import Response
    return Response(content=buf.tobytes(), media_type="image/jpeg")
