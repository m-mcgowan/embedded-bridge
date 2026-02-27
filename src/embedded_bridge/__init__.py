"""Structured communication with embedded devices."""

from .receivers import (
    CrashDetector,
    CrashEvent,
    CrashPattern,
    ESP32_PATTERNS,
    EventCapture,
    EventSpan,
    TraceEvent,
    Router,
    SleepEvent,
    SleepWakeMonitor,
)

__all__ = [
    "CrashDetector",
    "CrashEvent",
    "CrashPattern",
    "ESP32_PATTERNS",
    "EventCapture",
    "EventSpan",
    "TraceEvent",
    "Router",
    "SleepEvent",
    "SleepWakeMonitor",
]
