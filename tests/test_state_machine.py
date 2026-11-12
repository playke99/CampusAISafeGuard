"""状态机冒烟测试 —— 不依赖视频, 直接喂数据验证状态转移

场景: 人在座 -> 暂离(有物品) -> 超宽限计时 -> 超阈值疑似占座(事件生成)
     -> 持续不重复报 -> 人回座自动关单 -> 再次触发 repeat_count=2
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from seat_monitor.seat_state import SeatStateMachine, SeatStatus


class FakeSettings:
    """模拟配置: 全部时间参数压缩到秒级便于测试"""
    def __init__(self):
        self.d = {
            "grace_seconds": 5,             # 5s 宽限
            "absent_threshold_minutes": 0.2,  # 0.2min = 12s 触发
            "absent_confirm_frames": 2,
            "require_items": True,
            "seat_thresholds": {},
            "camera_id": "test-cam",
            "background": {"assist_items": True},
        }
    def get(self, k):
        return self.d.get(k)


class FakeDetector:
    items_enabled = True


class FakeMachine(SeatStateMachine):
    """跳过快照落盘"""
    def _persist_event(self, event):
        pass


ROI = {"seat_id": "A01", "zone_name": "自习区A01", "polygon": [[0.3, 0.3], [0.7, 0.3], [0.7, 0.7], [0.3, 0.7]]}

m = FakeMachine.__new__(FakeMachine)
m.detector = FakeDetector()
m.settings = FakeSettings()
m.reservation_store = None
m.snapshot_dir = Path("test_snapshots")
m.snapshot_dir.mkdir(exist_ok=True)
m.seats = {}
m._last_frame = None
m.sync_rois([ROI])
st = m.seats["A01"]

import numpy as np
frame = np.zeros((480, 640, 3), dtype=np.uint8)

# 人在座: 关键点(0.5,0.5) 在 ROI 内
p_in = type("P", (), {"bbox": (0, 0, 10, 10), "upper_body_points": lambda self: [(0.5 * 640, 0.5 * 480)]})()
# 人离开
p_none = []

def step(persons, items, label):
    evs = m.process(frame, persons, items)
    print(f"[{label:24s}] status={st.status.value:9s} absent={st.snapshot(time.time())['absent_minutes']:5.1f}min events={len(evs)}")
    return evs

# 物品 bbox 放在 ROI(192,144)-(448,336) 内部
BAG = lambda name: type("I", (), {"bbox": (280, 200, 340, 260), "name": name})()

print("== 阶段1: 人在座 ==")
for _ in range(3):
    step([p_in], [], "人在座")

print("== 阶段2: 人离开但有物品(暂离) ==")
for _ in range(2):
    step(p_none, [], "人不在(无物品)")   # 无物品 -> FREE? require_items=True 时人不在+无物品=FREE
step(p_none, [BAG("背包")], "人不在+有物品")
time.sleep(2)
for _ in range(2):
    step(p_none, [BAG("背包")], "暂离期(2s<5s宽限)")

print("== 阶段3: 超过宽限(等待 4s, 累计>5s) 进入计时 ==")
time.sleep(4)
step(p_none, [BAG("背包")], "计时中")
assert st.status == SeatStatus.TIMING, "应进入 TIMING"

print("== 阶段4: 超过阈值(12s) 触发疑似占座 ==")
time.sleep(8)
evs = step(p_none, [BAG("背包")], "超阈值")
assert st.status == SeatStatus.SUSPECT, "应触发 SUSPECT"
assert len(evs) == 1, "应生成 1 个事件"
ev1 = evs[0]
ev1_absent = ev1["absent_minutes"]   # 复制当前值(suspect_event 与 ev1 同引用)
print("   事件:", ev1["event_id"], "缺席", ev1_absent, "分钟")

print("== 阶段5: 持续缺席不应重复报 ==")
time.sleep(2)
evs = step(p_none, [BAG("背包")], "持续缺席")
assert len(evs) == 0, "SUSPECT 状态不应重复生成事件"
assert st.suspect_event["absent_minutes"] > ev1_absent, "持续时长应更新"

print("== 阶段6: 人回座 -> 自动关单 ==")
time.sleep(1)
evs = step([p_in], [], "人回座")
assert st.status == SeatStatus.OCCUPIED
assert st.suspect_event is None
print("   事件状态:", "已自动关闭(人已回座)")

print("== 阶段7: 再次触发 repeat_count=2 ==")
step(p_none, [], "无物品")
step(p_none, [BAG("书本")], "有物品")
time.sleep(13)
evs = step(p_none, [BAG("书本")], "再次超阈值")
assert len(evs) == 1 and evs[0]["repeat_count"] == 2, f"应生成第2次事件, got {evs}"

print()
print("ALL PASS")
