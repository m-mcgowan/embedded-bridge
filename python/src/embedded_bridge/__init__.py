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
from .testing import TestInfo, TestOutcome, TestSession
from .transport import Transport

__all__ = [
    "CrashDetector",
    "CrashEvent",
    "CrashPattern",
    "ESP32_PATTERNS",
    "EventCapture",
    "EventSpan",
    "TestInfo",
    "TestOutcome",
    "TestSession",
    "TraceEvent",
    "Router",
    "SleepEvent",
    "SleepWakeMonitor",
    "Transport",
]
