"""计划3 配置测试：按座位自定义霸座阈值可持久化、可清除。"""
from seat_monitor.config import Settings


def test_seat_thresholds_persist_and_clear(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{}", encoding="utf-8")
    settings = Settings(path)
    assert settings.get("seat_thresholds") == {}

    settings.update({"seat_thresholds": {"A01": 15}})
    reloaded = Settings(path)
    assert reloaded.get("seat_thresholds") == {"A01": 15}
    assert reloaded.get("absent_threshold_minutes") == 25  # 全局阈值不受影响

    reloaded.update({"seat_thresholds": {}})
    assert Settings(path).get("seat_thresholds") == {}
