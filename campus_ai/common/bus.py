"""消息总线抽象：默认内存总线（单机/测试/演示），预留 Kafka 生产对接。"""
from __future__ import annotations

import json
from typing import Callable, Protocol

from .events import BaseEvent

Handler = Callable[[BaseEvent], None]

TOPIC_RAW = "safety.raw"        # 原始感知事件
TOPIC_ALERT = "safety.alert"    # 复核后告警
TOPIC_ORDER = "safety.order"    # 工单
TOPIC_REPORT = "safety.report"  # Agent 结构化报告


class MessageBus(Protocol):
    def publish(self, topic: str, event: BaseEvent) -> None: ...
    def subscribe(self, topic: str, handler: Handler) -> None: ...


class InMemoryBus:
    """进程内同步总线：测试与演示场景使用，流转结果可直接断言。"""

    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = {}
        self.published: list[tuple[str, BaseEvent]] = []

    def publish(self, topic: str, event: BaseEvent) -> None:
        self.published.append((topic, event))
        for handler in self._subs.get(topic, []):
            handler(event)

    def subscribe(self, topic: str, handler: Handler) -> None:
        self._subs.setdefault(topic, []).append(handler)

    def events_of(self, topic: str) -> list[BaseEvent]:
        return [event for t, event in self.published if t == topic]


class KafkaBus:
    """Kafka 总线：生产环境对接消息队列；依赖缺失或集群不可用时构造即报错。"""

    def __init__(self, bootstrap_servers: str = "localhost:9092") -> None:
        try:
            from kafka import KafkaProducer  # type: ignore
        except ImportError as exc:
            raise RuntimeError("kafka-python 未安装，无法使用 KafkaBus") from exc
        self._producer = KafkaProducer(
            bootstrap_servers=bootstrap_servers,
            value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
        )

    def publish(self, topic: str, event: BaseEvent) -> None:
        self._producer.send(topic, event.to_dict())

    def subscribe(self, topic: str, handler: Handler) -> None:
        raise NotImplementedError("Kafka 消费由后端/Flink 承担，扩展包不做订阅")
