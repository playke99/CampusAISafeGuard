"""seat_monitor 服务入口

用法(conda yolo 环境):
    python -m seat_monitor.main                 # 使用默认 seat_monitor/settings.json
    python -m seat_monitor.main --config path   # 指定配置

视频源(settings.source): "0"=本机摄像头 / "rtsp://..."=网络摄像头 / "*.mp4"=本地文件
架构: 采集线程 -> 推理线程 -> 状态引擎线程 -> 事件推送线程（见 pipeline.py）
"""
import argparse
import logging
from pathlib import Path

from .background import BackgroundModel
from .capture import LatestFrameSlot
from .config import load_settings
from .detector import build_detector
from .pipeline import build_pipeline
from .preprocess import FramePreprocessor
from .reservation import ReservationStore
from .roi_store import RoiStore
from .seat_state import SeatStateMachine

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("seat_monitor")


class MonitorContext:
    """全局共享上下文（只读共享给 API；状态机由状态引擎线程单写）"""

    def __init__(self, settings, capture_opener=None):
        self.settings = settings
        base = Path(settings.path.parent)
        self.roi_store = RoiStore(base / "rois.json")
        self.reservations = ReservationStore(base / "reservations.json")
        pose_w = settings.resolve(settings.get("pose_weights"))
        detect_w = settings.resolve(settings.get("detect_weights"))
        logger.info("校验检测权重: pose=%s detect=%s", pose_w, detect_w)
        # build_detector 永不抛异常：缺失/损坏/加载失败自动降级
        self.detector = build_detector(pose_w, detect_w,
                                       conf=float(settings.get("conf")))
        det_status = getattr(self.detector, "status", {})
        if det_status.get("degraded"):
            logger.error("检测器降级: %s", det_status.get("message"))
        else:
            logger.info("检测器模式: %s", det_status.get("mode_cn", "?"))
        self.machine = SeatStateMachine(self.detector, settings, base / "snapshots",
                                        reservation_store=self.reservations)
        self.machine.sync_rois(self.roi_store.all())
        # 帧预处理 + 背景建模（遗留物辅助证据）
        self.preprocessor = FramePreprocessor(settings.get("preprocess"))
        self.background = BackgroundModel(settings.get("background"))
        logger.info("预处理: %s；背景建模: %s",
                    self.preprocessor.stats().get("cv2_available") and "cv2" or "直通",
                    self.background.stats().get("backend"))
        self.slot = LatestFrameSlot()
        self.pipeline = build_pipeline(
            settings, self.machine, self.detector, slot=self.slot,
            roi_provider=self.roi_store.all,
            capture_opener=capture_opener,
            preprocessor=self.preprocessor, background=self.background)

    # ── 帧共享（API/MJPEG 读取） ──────────────────────────
    def get_frame(self, annotated: bool = False):
        frame = self.slot.get_annotated() if annotated else self.slot.get_raw()
        return None if frame is None else frame.copy()

    def on_rois_changed(self):
        self.machine.sync_rois(self.roi_store.all())

    def health(self) -> dict:
        return self.pipeline.health()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=None, help="settings.json 路径")
    args = parser.parse_args()

    settings = load_settings(args.config)
    ctx = MonitorContext(settings)
    started = ctx.pipeline.start_all()
    logger.info("流水线已启动: %s", started)

    import uvicorn
    from .api import create_app
    app = create_app(ctx)
    port = int(settings.get("http_port"))
    logger.info("seat_monitor API 启动: http://127.0.0.1:%d", port)
    try:
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
    finally:
        ctx.pipeline.stop_all()
        logger.info("流水线已全部停止")


if __name__ == "__main__":
    main()
