"""人脸匹配：对接学校人脸识别系统，定位学生专业/班级/身份。

- HttpFaceMatchClient：POST 人脸特征/截图到学校系统接口。
- MockFaceMatcher：内置学生库（face_ref 为学号，演示约定）。
匹配不到返回 unknown，事件仍记录但标记待人工确认。
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any, Protocol


class FaceMatcher(Protocol):
    def match(self, face_ref: str) -> dict[str, Any]:
        """返回 {student_id, name, major, class_name, confidence} 或 unknown。"""
        ...


UNKNOWN = {"student_id": "", "name": "unknown", "major": "", "class_name": "",
           "confidence": 0.0}


class HttpFaceMatchClient:
    def __init__(self, url: str, token: str = "", timeout: float = 3.0) -> None:
        self.url = url
        self.token = token
        self.timeout = timeout

    def match(self, face_ref: str) -> dict[str, Any]:
        payload = json.dumps({"face_ref": face_ref}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.url, data=payload, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return {**UNKNOWN, **data, "confidence": float(data.get("confidence", 0))}
        except Exception:  # noqa: BLE001 - 对接失败按未匹配处理，不阻断事件记录
            return dict(UNKNOWN)


class MockFaceMatcher:
    def __init__(self, students: list[dict[str, str]] | None = None) -> None:
        default = [
            {"student_id": "2023001", "name": "张三", "major": "软件技术",
             "class_name": "软件2301"},
            {"student_id": "2023002", "name": "李四", "major": "大数据技术",
             "class_name": "大数据2302"},
        ]
        self._db = {s["student_id"]: s for s in (students or default)}

    def match(self, face_ref: str) -> dict[str, Any]:
        student = self._db.get(face_ref)
        if not student:
            return dict(UNKNOWN)
        return {**student, "confidence": 0.97}
