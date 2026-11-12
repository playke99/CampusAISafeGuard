"""演示场景执行器：视觉预警 + 四大 AI 业务场景统一编排。

所有场景返回统一结构：
{"title","steps","alerts","events","pushes","orders","violations","report_text","audio"}

视觉预警由 ai.alert_engine 产出；其余场景复用 campus_ai.CampusRuntime。
"""
from __future__ import annotations

import json
import os
from typing import Any

from ai.alert_engine import AlertEngine
from demo import pose_builder
from server.core.config import resolve_path

DEFAULT_BEHAVIOR = {
    "camera_id": "teach-cam-01", "location": "教学楼A栋入口",
    "frames": [
        {"frame_id": "frame_1", "detections": [
            {"label": "slippers", "conf": 0.92, "bbox": [12, 30, 88, 140],
             "face_ref": "2023001"},
            {"label": "breakfast", "conf": 0.87, "bbox": [200, 40, 260, 150],
             "face_ref": "2023002"},
            {"label": "slippers", "conf": 0.81, "bbox": [320, 50, 390, 160],
             "face_ref": "unknown_face"}]},
        {"frame_id": "frame_2", "detections": []},
    ],
}

DEFAULT_SEAT = {
    "camera_id": "lib-cam-03", "location": "图书馆3层自习区",
    "threshold_minutes": 25,
    "seats": {"L-301": {"item": "书包和水杯", "absent_minutes": 35,
                        "repeat_count": 1},
              "L-302": {"item": "笔记本电脑", "absent_minutes": 15,
                        "repeat_count": 1}},
}

CATALOG = [
    {"name": "vision_attack", "title": "视觉预警 · 暴力攻击", "group": "视觉感知",
     "desc": "手腕位移 Z-Score 突增，触发红色预警并自动语音震慑"},
    {"name": "vision_fall", "title": "视觉预警 · 人员摔倒", "group": "视觉感知",
     "desc": "躯干倾斜角超过 65°，触发橙色预警并通知校医"},
    {"name": "vision_surround", "title": "视觉预警 · 多人围堵", "group": "视觉感知",
     "desc": "多人间距小于 3.5 倍肩宽，触发黄色预警"},
    {"name": "vision_bullying", "title": "视觉预警 · 霸凌冲突", "group": "视觉感知",
     "desc": "围堵 + 攻击多信号叠加，自动升级红色并启动干预预案"},
    {"name": "agent", "title": "智能体 · 多模态研判", "group": "AI 业务",
     "desc": "真实音频转写 + LLM 深判 + 结构化报告 + 定向推送"},
    {"name": "intercom", "title": "对讲 · 危险词干预", "group": "AI 业务",
     "desc": "实时收音转写，命中危险词即刻告警并播放制止话术"},
    {"name": "behavior", "title": "行为规范 · 人脸定位", "group": "AI 业务",
     "desc": "违规行为检出 + 人脸匹配定位 + 未识别转人工复核"},
    {"name": "seat", "title": "图书馆 · 占座工单", "group": "扩展场景",
     "desc": "超时占座自动生成工单，推送图书管理员闭环"},
]


class ScenarioRunner:
    def __init__(self, config: dict[str, Any], engine: AlertEngine,
                 runtime: Any | None = None) -> None:
        self.config = config
        self.engine = engine
        self.runtime = runtime
        self.audio_dir = resolve_path(config["app"].get("audio_dir", "audio_assets"))

    def catalog(self) -> list[dict[str, Any]]:
        return CATALOG

    def run(self, name: str) -> dict[str, Any]:
        if name.startswith("vision_"):
            return self._run_vision(name.split("_", 1)[1])
        handler = {
            "agent": self._run_agent,
            "intercom": self._run_intercom,
            "behavior": self._run_behavior,
            "seat": self._run_seat,
        }.get(name)
        if not handler:
            raise KeyError(name)
        return handler()

    # ---------- 视觉预警 ----------
    def _run_vision(self, scene: str) -> dict[str, Any]:
        frames, camera_id, location, title = pose_builder.build(scene)
        alerts = self.engine.analyze_vision(frames, camera_id, location,
                                            force=True)
        steps = [
            {"label": "接入监控画面窗口", "ok": True,
             "detail": f"{camera_id} · {location} · {len(frames)} 帧关键点序列"},
            {"label": "姿态估计与行为判定", "ok": bool(alerts),
             "detail": (alerts[0]["evidence"].get("signal_text", "")
                        if alerts else "未检出异常")},
        ]
        if alerts:
            alert = alerts[0]
            steps += [
                {"label": "生成分级预警", "ok": True,
                 "detail": f"{alert['severity']} · 置信度 {alert['confidence']} · "
                           f"{alert['alert_id']}"},
                {"label": "自动干预动作", "ok": True,
                 "detail": ("已下发语音震慑并调度安保"
                            if alert.get("auto_action") else "已推送值班人员")},
            ]
            report = self.engine.analyzer.deep_report(alert)
        else:
            report = ""
        return {"title": f"视觉预警 · {title}", "steps": steps,
                "alerts": alerts, "events": [], "pushes": [], "orders": [],
                "violations": [], "report_text": report, "audio": []}

    # ---------- 智能体 ----------
    def _run_agent(self) -> dict[str, Any]:
        audio = self._audio("red_01.wav")
        payload: dict[str, Any] = {"location": "宿舍楼B座2层楼道"}
        if audio:
            payload["audio_path"] = audio
        else:
            payload["transcript"] = "救命！别打我，快帮帮我！"
        result = self.runtime.process_media(payload) if self.runtime else None
        if not result:
            return self._degraded("智能体 · 多模态研判")
        event = result["event"]
        alerts = []
        if result["severity"] in ("red", "orange"):
            alerts.append(self.engine.alert_from_event(
                event, alert_type="bullying", source="agent"))
        steps = [
            {"label": "接收多模态素材", "ok": True,
             "detail": f"{'真实音频 red_01.wav' if audio else '转写文本'} + 姿态数据"},
            {"label": "语音转写", "ok": bool(result.get("transcript")),
             "detail": result.get("transcript") or "（无转写）"},
            {"label": "LLM 深判", "ok": True,
             "detail": f"级别 {result['severity']} · 类型 bullying_suspected"},
            {"label": "生成结构化报告", "ok": bool(result.get("report_text")),
             "detail": "含摘要 / 时间线 / 涉及人数 / 处置建议"},
            {"label": "定向推送", "ok": bool(result.get("push_ok")),
             "detail": f"→ {', '.join(result.get('targets') or [])}"},
        ]
        pushes = [{"event_id": event["event_id"], "severity": event["severity"],
                   "targets": result.get("targets", []),
                   "summary": event.get("summary", "")}]
        return {"title": "智能体 · 多模态研判", "steps": steps, "alerts": alerts,
                "events": [event], "pushes": pushes, "orders": [],
                "violations": [], "report_text": result.get("report_text", ""),
                "audio": ([{"src": "/audio_assets/red_01.wav",
                            "label": "现场音频 red_01.wav"}] if audio else [])}

    # ---------- 对讲危险词 ----------
    def _run_intercom(self) -> dict[str, Any]:
        audio = self._audio("orange_01.wav")
        payload = ({"audio_path": audio} if audio
                   else {"transcript": "别打我，放过我，我错了"})
        result = self.runtime.handle_utterance(payload) if self.runtime else None
        if not result:
            return self._degraded("对讲 · 危险词干预")
        event = result["event"]
        alert = self.engine.alert_from_event(event, "danger_word", "intercom")
        steps = [
            {"label": "对讲收音", "ok": True,
             "detail": "orange_01.wav" if audio else "转写文本模拟"},
            {"label": "实时转写", "ok": True, "detail": result["transcript"]},
            {"label": "危险词匹配", "ok": True,
             "detail": f"命中 {','.join(result['matched'])}（{result['severity']}）"},
            {"label": "定向推送", "ok": bool(result["push_ok"]),
             "detail": "安保值班员 / 班主任"},
            {"label": "现场口头制止", "ok": bool(result["deterrence_played"]),
             "detail": result.get("deterrence_phrase", "")},
        ]
        pushes = [{"event_id": event["event_id"], "severity": event["severity"],
                   "targets": event.get("targets", []),
                   "summary": event.get("summary", "")}]
        audio_list = []
        if audio:
            audio_list.append({"src": "/audio_assets/orange_01.wav",
                               "label": "收音样本（威胁用语）"})
        return {"title": "对讲 · 危险词干预", "steps": steps, "alerts": [alert],
                "events": [event], "pushes": pushes, "orders": [],
                "violations": [], "report_text": "", "audio": audio_list}

    # ---------- 行为规范 ----------
    def _run_behavior(self) -> dict[str, Any]:
        data = self._load_json("behavior_frames.json", DEFAULT_BEHAVIOR)
        results: list[dict[str, Any]] = []
        if self.runtime:
            for frame in data.get("frames", []):
                results.extend(self.runtime.ingest_behavior(
                    {"detections": frame.get("detections", [])}))
        events = [r["event"] for r in results]
        pushes = [{"event_id": r["event"]["event_id"],
                   "severity": r["event"]["severity"],
                   "targets": r["event"].get("targets", []),
                   "summary": r["event"].get("summary", "")}
                  for r in results]
        identified = [r for r in results if r["identified"]]
        review = [r for r in results if not r["identified"]]
        steps = [
            {"label": "加载检测帧与人脸库", "ok": True,
             "detail": f"{data.get('camera_id')} · {data.get('location')} · "
                       f"{len(results)} 条检出"},
            {"label": "行为规范模型检出", "ok": len(results) == 3,
             "detail": "slippers×2 + breakfast×1"},
            {"label": "人脸匹配定位", "ok": len(identified) == 2,
             "detail": f"{len(identified)} 人已定位"},
            {"label": "未识别转人工复核", "ok": len(review) == 1,
             "detail": f"{len(review)} 人 marked needs_review"},
        ]
        return {"title": "行为规范 · 人脸定位", "steps": steps, "alerts": [],
                "events": events, "pushes": pushes, "orders": [],
                "violations": results, "report_text": "", "audio": []}

    # ---------- 图书馆占座 ----------
    def _run_seat(self) -> dict[str, Any]:
        data = self._load_json("seat_demo.json", DEFAULT_SEAT)
        seats = data.get("seats", {})
        threshold = int(data.get("threshold_minutes", 25))
        over = {sid: meta for sid, meta in seats.items()
                if int(meta.get("absent_minutes", 0)) >= threshold}
        under = {sid: meta for sid, meta in seats.items() if sid not in over}
        events, orders = [], []
        if over and self.runtime:
            sid, meta = next(iter(over.items()))
            result = self.runtime.handle_seat_event({
                "camera_id": data.get("camera_id"),
                "seat_id": sid,
                "zone_name": data.get("location"),
                "absent_minutes": meta.get("absent_minutes"),
                "items": [meta.get("item", "遗留物品")],
                "repeat_count": meta.get("repeat_count", 1),
            })
            events.append(result["event"])
            orders.extend(result["orders"])
        steps = [
            {"label": "载入占座监测剧本", "ok": True,
             "detail": f"{data.get('location')} · 阈值 {threshold} 分钟 · "
                       f"{len(seats)} 个座位"},
        ]
        for sid, meta in under.items():
            steps.append({"label": f"{sid}：未超阈值",
                          "ok": True,
                          "detail": f"物品 {meta.get('item')} 滞留 "
                                    f"{meta.get('absent_minutes')} 分钟，计时复位"})
        for sid, meta in over.items():
            steps.append({"label": f"{sid}：超时占座",
                          "ok": True,
                          "detail": f"物品 {meta.get('item')} 滞留 "
                                    f"{meta.get('absent_minutes')} 分钟 → 生成工单"})
        steps.append({"label": "推送图书管理员", "ok": bool(orders),
                      "detail": "工单状态 pending，一座一单不重复推送"})
        return {"title": "图书馆 · 占座工单", "steps": steps, "alerts": [],
                "events": events, "pushes": [], "orders": orders,
                "violations": [], "report_text": "", "audio": []}

    # ---------- 工具 ----------
    def _audio(self, filename: str) -> str | None:
        path = os.path.join(self.audio_dir, filename)
        return path if os.path.exists(path) else None

    def _load_json(self, name: str, default: dict[str, Any]) -> dict[str, Any]:
        path = resolve_path(os.path.join("demo", "data", name))
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, ValueError):
                pass
        return default

    @staticmethod
    def _degraded(title: str) -> dict[str, Any]:
        return {"title": title,
                "steps": [{"label": "运行时不可用", "ok": False,
                           "detail": "campus_ai 运行时未初始化，请检查依赖"}],
                "alerts": [], "events": [], "pushes": [], "orders": [],
                "violations": [], "report_text": "", "audio": []}
