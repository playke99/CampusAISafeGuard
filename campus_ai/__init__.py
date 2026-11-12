"""校园安全 AI 扩展包（本地 Agent / 对讲 / 霸座工单 / 违规识别）。

四个子模块共享同一套事件模型、消息总线、推送网关与归档存储：
- agent_service     非结构化音视频 → 结构化报告 → 定向推送
- intercom_service  收音 → 语音转写 → 危险词告警 → 口头制止
- seat_service      霸座事件 → 待处理工单 → 图书管理员端
- behavior_service  违规行为识别 → 人脸匹配 → Agent 推送归档
"""
