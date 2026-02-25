"""Structured communication with embedded devices."""

from .receivers import CrashDetector, CrashEvent, CrashPattern, ESP32_PATTERNS, Router

__all__ = [
    "CrashDetector",
    "CrashEvent",
    "CrashPattern",
    "ESP32_PATTERNS",
    "Router",
]
