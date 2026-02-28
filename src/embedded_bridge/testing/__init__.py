"""Test protocol for embedded devices.

Provides host-side infrastructure for discovering and running tests on
embedded devices via serial. Firmware emits JSON metadata and T= timing
markers; this module parses them and manages the test lifecycle.

Usage::

    from embedded_bridge.testing import TestSession, TestInfo, TestOutcome
"""

from .protocol import SOH, STX, parse_json_line, parse_marker
from .session import TestSession
from .types import TestInfo, TestOutcome

__all__ = [
    "SOH",
    "STX",
    "TestInfo",
    "TestOutcome",
    "TestSession",
    "parse_json_line",
    "parse_marker",
]
