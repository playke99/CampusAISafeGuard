"""危险词库匹配：红/橙/黄分级，支持外部 JSON 配置。

匹配规则：文本归一化（去空白/标点、统一小写）后做子串匹配；
命中多个级别时取最高级别，返回命中词清单用于证据留档。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from campus_ai.common.events import Severity

_PUNCT = re.compile(r"[\s，。！？、,.!?;；:：'\"“”‘’·…\-—_]+")


def normalize(text: str) -> str:
    return _PUNCT.sub("", text).lower()


@dataclass
class MatchResult:
    hit: bool = False
    severity: Severity = Severity.BLUE
    matched: list[str] = field(default_factory=list)


class DangerWordMatcher:
    DEFAULT = {
        "red": ["救命", "报警", "110", "帮帮我"],
        "orange": ["别打我", "放过我", "我错了", "打死你", "弄死你", "找死"],
        "yellow": ["废物", "垃圾", "傻X", "滚"],
    }

    def __init__(self, words: dict[str, list[str]] | None = None) -> None:
        source = words or self.DEFAULT
        self._words: dict[Severity, list[str]] = {}
        for level, items in source.items():
            try:
                severity = Severity(level)
            except ValueError:
                continue
            seen: set[str] = set()
            for word in items:
                norm = normalize(str(word))
                if norm and norm not in seen:
                    seen.add(norm)
                    self._words.setdefault(severity, []).append(norm)

    @classmethod
    def from_file(cls, path: str) -> "DangerWordMatcher":
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return cls(json.load(f))
        return cls()

    def match(self, text: str) -> MatchResult:
        norm = normalize(text or "")
        if not norm:
            return MatchResult()
        for severity in (Severity.RED, Severity.ORANGE, Severity.YELLOW):
            hits = [word for word in self._words.get(severity, []) if word in norm]
            if hits:
                return MatchResult(hit=True, severity=severity, matched=hits)
        return MatchResult()
