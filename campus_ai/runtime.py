"""统一运行时：装配四个子模块，并对 API 暴露业务入口。

同一进程内共享事件总线、推送网关与归档目录；无后端时推送写入本地队列，
保证消息不丢失。测试可注入 RecordingPushGateway 断言推送对象。
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from campus_ai.agent_service.llm_client import MockLLMClient, OllamaClient
from campus_ai.agent_service.parsers import MediaBundle, MockASR
from campus_ai.agent_service.service import AgentService
from campus_ai.behavior_service.face_match import HttpFaceMatchClient, MockFaceMatcher
from campus_ai.behavior_service.inference import (Detection, MockBehaviorDetector,
                                                  YoloBehaviorDetector)
from campus_ai.behavior_service.service import BehaviorViolationService
from campus_ai.behavior_service.train import train as train_behavior_model
from campus_ai.common.archive import JsonlArchive
from campus_ai.common.bus import InMemoryBus, TOPIC_ORDER
from campus_ai.common.config import PROJECT_ROOT, load_config, resolve_path
from campus_ai.common.push_gateway import (HttpPushGateway, JsonlPushGateway,
                                           PushGateway)
from campus_ai.intercom_service.audio_io import (RecordingSpeakerSink,
                                                 ScriptedMicSource, SpeakerSink)
from campus_ai.intercom_service.danger_words import DangerWordMatcher
from campus_ai.intercom_service.service import IntercomService
from campus_ai.seat_service.orders import work_order_from_event


class CampusRuntime:
    def __init__(self, config: dict[str, Any] | None = None,
                 config_path: str | None = None,
                 gateway: PushGateway | None = None) -> None:
        self.config = config if config is not None else load_config(config_path)
        archive_root = resolve_path(self.config["storage"]["root"]) or str(PROJECT_ROOT / "archive")
        self.archive_dir = Path(archive_root)
        self.reports = JsonlArchive(self.archive_dir / "reports.jsonl")
        self.alerts = JsonlArchive(self.archive_dir / "alerts.jsonl")
        self.workorders = JsonlArchive(self.archive_dir / "workorders.jsonl")
        self.violations = JsonlArchive(self.archive_dir / "violations.jsonl")

        self.bus = InMemoryBus()
        self.gateway = gateway or self._build_gateway()
        self.asr = MockASR()
        self.llm = self._build_llm()
        agent_cfg = self.config["agent"]
        self.agent = AgentService(self.llm, self.asr, self.gateway, self.bus,
                                  agent_cfg["route"])

        intercom_cfg = self.config["intercom"]
        self.words_path = resolve_path(intercom_cfg["danger_words_file"])
        self.words = self._read_words()
        self.matcher = DangerWordMatcher(self.words)
        self.speaker = self._build_speaker()
        self.intercom = IntercomService(
            source=ScriptedMicSource([]), sink=self.speaker, asr=self.asr,
            matcher=self.matcher, gateway=self.gateway, bus=self.bus,
            device_id=intercom_cfg["device_id"],
            location=intercom_cfg["location"],
            deterrence_phrases=intercom_cfg["deterrence_phrases"],
        )

        behavior_cfg = self.config["behavior"]
        self.behavior = BehaviorViolationService(
            detector=self._build_detector(),
            face_matcher=self._build_face_matcher(),
            agent=self.agent, bus=self.bus,
            camera_id=behavior_cfg["camera_id"],
            location=behavior_cfg["location"],
            targets=behavior_cfg["targets"],
        )

    # ── 装配 ─────────────────────────────────────────────
    def _build_gateway(self) -> PushGateway:
        push = self.config["push"]
        if push.get("endpoint"):
            return HttpPushGateway(push["endpoint"], push.get("token", ""),
                                  float(push.get("timeout", 3.0)),
                                  int(push.get("max_retry", 3)))
        return JsonlPushGateway(self.archive_dir / "push_queue.jsonl")

    def _build_llm(self):
        cfg = self.config["agent"]
        if cfg.get("use_ollama"):
            return OllamaClient(cfg["base_url"], cfg["model"], float(cfg["timeout"]))
        return MockLLMClient()

    def _build_speaker(self):
        if self.config["intercom"].get("enable_tts"):
            try:
                return SpeakerSink()
            except RuntimeError:
                pass
        return RecordingSpeakerSink()

    def _build_detector(self):
        cfg = self.config["behavior"]
        model_path = resolve_path(cfg.get("model_path"))
        if model_path and Path(model_path).is_file():
            try:
                return YoloBehaviorDetector(model_path, float(cfg["conf_threshold"]))
            except RuntimeError:
                pass
        return MockBehaviorDetector([])

    def _build_face_matcher(self):
        cfg = self.config["behavior"]
        if cfg.get("face_match_url"):
            return HttpFaceMatchClient(cfg["face_match_url"],
                                       cfg.get("face_match_token", ""))
        return MockFaceMatcher(cfg.get("students"))

    def _read_words(self) -> dict[str, list[str]]:
        path = Path(self.words_path)
        if path.exists():
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        return json.loads(json.dumps(DangerWordMatcher.DEFAULT))

    # ── 计划1：音视频 → 结构化报告 → 推送 ──────────────────
    def process_media(self, payload: dict[str, Any]) -> dict[str, Any]:
        bundle = MediaBundle(
            event_id=payload.get("event_id") or f"MEDIA-{uuid.uuid4().hex[:8]}",
            location=payload.get("location") or self.config["agent"].get("location", "校园区域"),
            audio_path=payload.get("audio_path"),
            video_path=payload.get("video_path"),
            pose_data_path=payload.get("pose_data_path"),
            transcript=payload.get("transcript"),
        )
        result = self.agent.process(bundle)
        self.reports.append({
            "archived_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "event": result["event"],
            "report": result["report"],
            "report_text": result["report_text"],
        })
        return result

    # ── 计划3：霸座事件 → 待处理工单 → 管理员端 ─────────────
    def handle_seat_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        seat_cfg = self.config["seat_monitor"]
        order = work_order_from_event(payload, seat_cfg["location"],
                                      seat_cfg["staff_targets"])
        self.bus.publish(TOPIC_ORDER, order)
        push = self.gateway.push(order, order.targets)
        record = {
            "archived_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "event": order.to_dict(),
            "push_ok": push.get("ok", False),
        }
        self.workorders.append(record)
        text = (f"# 霸座处置工单\n\n"
                f"- 工单编号：{order.event_id}\n"
                f"- 座位：{payload.get('seat_id')}\n"
                f"- 区域：{payload.get('zone_name', seat_cfg['location'])}\n"
                f"- 滞留时长：{payload.get('absent_minutes', 0)} 分钟\n"
                f"- 遗留物品：{'、'.join(payload.get('items') or []) or '（无）'}\n"
                f"- 当日重复：第 {payload.get('repeat_count', 1)} 次\n"
                f"- 处置状态：待处理（已推送图书管理员）")
        self.reports.append({
            "archived_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "event": order.to_dict(),
            "report_text": text,
        })
        return {
            "orders": [{"order_id": order.event_id, "seat_id": payload.get("seat_id"),
                        "push_ok": push.get("ok", False)}],
            "degraded": not push.get("ok", False),
            "report_text": text,
            "event": order.to_dict(),
        }

    # ── 计划2：对讲收音 → 危险词告警 ────────────────────────
    def handle_utterance(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        result = self.intercom.handle_utterance(
            audio_path=payload.get("audio_path"), transcript=payload.get("transcript"))
        if result:
            self.alerts.append({
                "archived_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "event": result["event"],
                "push_ok": result["push_ok"],
                "deterrence_phrase": result["deterrence_phrase"],
            })
        return result

    # ── 计划4：违规识别 → 人脸匹配 → Agent 归档 ────────────
    def ingest_behavior(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        detections = [
            Detection(
                label=str(item.get("label", "")),
                conf=float(item.get("conf", 0.9)),
                bbox=tuple(item.get("bbox") or (0, 0, 1, 1)),  # type: ignore[arg-type]
                face_ref=item.get("face_ref"),
            )
            for item in payload.get("detections", [])
        ]
        results = self.behavior.process_detections(detections)
        for result in results:
            self.violations.append({
                "archived_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "event": result["event"],
                "push_ok": result["push_ok"],
                "needs_review": result["needs_review"],
            })
        return results

    def train_behavior(self, payload: dict[str, Any]) -> dict[str, Any]:
        data_root = resolve_path(payload.get("data_root", "datasets/behavior_demo"))
        return train_behavior_model(
            data_root,
            payload.get("classes") or self.config["behavior"]["classes"],
            int(payload.get("epochs", 100)),
            payload.get("model", "yolo11n.pt"),
            bool(payload.get("dry_run", True)),
        )

    # ── 词库热更新 ───────────────────────────────────────
    def reload_danger_words(self, words: dict[str, list[str]]) -> dict[str, list[str]]:
        path = Path(self.words_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(words, f, ensure_ascii=False, indent=2)
        self.words = words
        self.matcher = DangerWordMatcher(words)
        self.intercom.matcher = self.matcher
        return words

    def danger_words(self) -> dict[str, list[str]]:
        return self.words

    def stats(self) -> dict[str, int]:
        return {
            "reports": self.reports.count(),
            "alerts": self.alerts.count(),
            "workorders": self.workorders.count(),
            "violations": self.violations.count(),
        }
