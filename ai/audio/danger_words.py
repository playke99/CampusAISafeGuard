"""危险词分级匹配：红/橙/黄三级，支持外部词库热更新。

归一化（去空白/标点、统一小写）后子串匹配；多级命中取最高级。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

SEVERITIES = ("red", "orange", "yellow", "blue")
_PUNCT = re.compile(r"[\s，。！？、,.!?;；:：'\"“”‘’·…\-—_]+")


def normalize(text: str) -> str:
    return _PUNCT.sub("", text or "").lower()


@dataclass
class MatchResult:
    hit: bool = False
    severity: str = "blue"
    matched: list[str] = field(default_factory=list)


class DangerWordMatcher:
    DEFAULT = {
        "red": ["救命", "报警", "110", "帮帮我"],
        "orange": ["别打我", "放过我", "我错了", "打死你", "弄死你", "找死"],
        "yellow": ["废物", "垃圾", "傻X", "滚"],
    }

    def __init__(self, words: dict[str, list[str]] | None = None) -> None:
        source = words or self.DEFAULT
        self._words: dict[str, list[str]] = {s: [] for s in SEVERITIES}
        for level, items in source.items():
            if level not in self._words or not isinstance(items, list):
                continue
            seen: set[str] = set()
            for word in items:
                norm = normalize(str(word))
                if norm and norm not in seen:
                    seen.add(norm)
                    self._words[level].append(norm)

    @classmethod
    def from_file(cls, path: str) -> "DangerWordMatcher":
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return cls(json.load(f))
        return cls()

    def match(self, text: str) -> MatchResult:
        norm = normalize(text)
        if not norm:
            return MatchResult()
        for severity in ("red", "orange", "yellow"):
            hits = [w for w in self._words.get(severity, []) if w in norm]
            if hits:
                return MatchResult(True, severity, hits)
        return MatchResult()
