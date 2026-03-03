"""Test protocol constants and parsing.

The protocol uses control characters outside the normal ASCII printable range
to avoid collision with interactive menu keys:

- SOH (\\x01): "list tests" — device responds with test_catalog JSON
- STX (\\x02): "run test" — followed by test ID and newline

Firmware emits structured markers during test execution:

JSON lines (metadata):
    {"type":"test_catalog","tests":[...]}
    {"type":"test_start","id":"sleep_w2","name":"..."}
    {"type":"test_end","id":"sleep_w2","status":"ok"}

T= markers (timing-critical):
    T=<ts> TEST_STARTED:<id>
    T=<ts> TEST_STOPPED:<id>
    T=<ts> PPK_START
    T=<ts> PPK_STOP
    T=<ts> SLEEP:<duration_seconds>
"""

import json
import re
from typing import Any

# Control characters
SOH = b"\x01"  # List tests
STX = b"\x02"  # Run test (followed by id + newline)
ETX = b"\x03"  # Configure fixture (followed by JSON + newline)

# T= marker patterns for protocol-specific events.
# These extend the existing T=<ts> <NAME>_STARTED/STOPPED format used by
# EventCapture, adding colon-delimited payloads for test ID and sleep duration.
_MARKER_PATTERN = re.compile(
    r"^T=(\d+)(?:\.(\d+))?\s+(.+)$"
)


def parse_marker(line: str) -> tuple[float, str] | None:
    """Parse a T= marker line into (timestamp_s, payload).

    Returns None if the line doesn't match.

    Examples::

        >>> parse_marker("T=1234567 PPK_START")
        (1.234567, 'PPK_START')
        >>> parse_marker("T=1234567 SLEEP:30")
        (1.234567, 'SLEEP:30')
        >>> parse_marker("T=1234567 TEST_STARTED:sleep_w2")
        (1.234567, 'TEST_STARTED:sleep_w2')
        >>> parse_marker("not a marker") is None
        True
    """
    m = _MARKER_PATTERN.match(line.strip())
    if not m:
        return None

    whole = int(m.group(1))
    frac_str = m.group(2) or "0"
    # Normalize fractional part: "1234567" → 1.234567, "001600" → 0.001600
    frac = int(frac_str) / (10 ** len(frac_str))
    timestamp = whole + frac

    return (timestamp, m.group(3))


def parse_json_line(line: str) -> dict[str, Any] | None:
    """Try to parse a line as a JSON object.

    Returns None if the line isn't valid JSON or isn't a dict.
    """
    stripped = line.strip()
    if not stripped.startswith("{"):
        return None
    try:
        obj = json.loads(stripped)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, ValueError):
        pass
    return None
