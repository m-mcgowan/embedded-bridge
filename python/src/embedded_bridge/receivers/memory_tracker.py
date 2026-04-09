"""Per-test heap memory tracking for embedded devices.

Parses ``ETST:MEM:*`` markers emitted by test frameworks that report heap
statistics before and after each test case. Identifies memory leaks
by tracking the delta between pre- and post-test heap sizes.

Wire format (from device)::

    ETST:MEM:BEFORE free=<N> min=<N> *XX
    ETST:MEM:AFTER free=<N> delta=<+/-N> min=<N> *XX
    ETST:MEM:WARN leaked=<N> *XX

The prefix is configurable via ``set_prefix()`` for compatibility
with other protocol conventions.
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Default protocol prefix. Overridable via set_prefix().
_DEFAULT_PREFIX = "ETST:"

# Import protocol parser from pio_test_runner if available,
# otherwise use a minimal inline implementation.
try:
    from pio_test_runner.protocol import parse_line, parse_payload
except ImportError:
    import re as _re

    _TOKEN_RE = _re.compile(
        r'(\w+)="([^"]*)"' r"|(\w+)=(\S+)" r"|(\w+)"
    )

    class _ParsedTag:
        def __init__(self, tag, payload_str, crc_valid, raw):
            self.tag = tag
            self.payload_str = payload_str
            self.crc_valid = crc_valid
            self.raw = raw

    def _crc8(data: str) -> int:
        crc = 0x00
        for byte in data.encode("utf-8"):
            crc ^= byte
            for _ in range(8):
                if crc & 0x80:
                    crc = ((crc << 1) ^ 0x31) & 0xFF
                else:
                    crc = (crc << 1) & 0xFF
        return crc

    # Compiled regexes are rebuilt when prefix changes.
    _prefix = _DEFAULT_PREFIX
    _line_re = None
    _line_no_crc_re = None

    def _compile_patterns(prefix):
        global _prefix, _line_re, _line_no_crc_re
        _prefix = prefix
        escaped = _re.escape(prefix)
        _line_re = _re.compile(
            rf"^{escaped}(\S+?)(?:\s+(.*?))?\s+\*([0-9A-Fa-f]{{2}})$"
        )
        _line_no_crc_re = _re.compile(
            rf"^{escaped}(\S+?)(?:\s+(.*))?$"
        )

    _compile_patterns(_DEFAULT_PREFIX)

    def parse_line(line):
        stripped = line.strip()
        if not stripped.startswith(_prefix):
            return None
        m = _line_re.match(stripped)
        if m:
            tag, payload_str, crc_hex = m.group(1), m.group(2) or "", m.group(3)
            content = stripped[: stripped.rfind(f" *{crc_hex}")]
            valid = _crc8(content) == int(crc_hex, 16)
            return _ParsedTag(tag, payload_str, valid, stripped)
        m = _line_no_crc_re.match(stripped)
        if m:
            return _ParsedTag(m.group(1), m.group(2) or "", None, stripped)
        return None

    def parse_payload(payload_str):
        result = {}
        for m in _TOKEN_RE.finditer(payload_str):
            if m.group(1) is not None:
                result[m.group(1)] = m.group(2)
            elif m.group(3) is not None:
                result[m.group(3)] = m.group(4)
            elif m.group(5) is not None:
                result[m.group(5)] = True
        return result


def set_prefix(prefix: str) -> None:
    """Change the protocol prefix (e.g. "ETST:" or "PTR:").

    Affects the inline fallback parser. When pio_test_runner.protocol
    is available, update its PREFIX constant instead.
    """
    global _DEFAULT_PREFIX
    _DEFAULT_PREFIX = prefix
    # Update inline parser if it's the active one
    if "_compile_patterns" in dir():
        _compile_patterns(prefix)


@dataclass
class MemoryInfo:
    """Heap statistics for a single test case."""

    free_before: int = 0
    min_before: int = 0
    free_after: int = 0
    min_after: int = 0
    delta: int = 0


class MemoryTracker:
    """Receiver that tracks per-test heap memory usage.

    Call ``set_current_test()`` when a new test starts (e.g. from a
    ``ETST:CASE:START`` marker). The tracker pairs ``ETST:MEM:BEFORE``
    and ``ETST:MEM:AFTER`` lines with the current test name.

    Args:
        leak_threshold: Minimum negative delta (in bytes) to consider
            a leak. Default -1000 (1 KB).
    """

    def __init__(self, leak_threshold: int = -1000) -> None:
        self._leak_threshold = leak_threshold
        self._current_test: str = ""
        self._data: dict[str, MemoryInfo] = {}

    def feed(self, message: bytes | str) -> None:
        """Feed a line of device output."""
        line = (
            message.decode("utf-8", errors="replace")
            if isinstance(message, bytes)
            else message
        )

        parsed = parse_line(line)
        if not parsed or parsed.crc_valid is False:
            return

        if parsed.tag == "MEM:BEFORE" and self._current_test:
            payload = parse_payload(parsed.payload_str)
            info = self._data.setdefault(self._current_test, MemoryInfo())
            free_str = payload.get("free", "0")
            min_str = payload.get("min", "0")
            info.free_before = int(free_str) if isinstance(free_str, str) else 0
            info.min_before = int(min_str) if isinstance(min_str, str) else 0
            return

        if parsed.tag == "MEM:AFTER" and self._current_test:
            payload = parse_payload(parsed.payload_str)
            info = self._data.setdefault(self._current_test, MemoryInfo())
            free_str = payload.get("free", "0")
            delta_str = payload.get("delta", "0")
            min_str = payload.get("min", "0")
            info.free_after = int(free_str) if isinstance(free_str, str) else 0
            info.delta = int(delta_str) if isinstance(delta_str, str) else 0
            info.min_after = int(min_str) if isinstance(min_str, str) else 0
            return

    def set_current_test(self, name: str) -> None:
        """Set the test name for subsequent MEM lines."""
        self._current_test = name

    @property
    def all_tests(self) -> dict[str, MemoryInfo]:
        """All tracked tests and their memory info."""
        return dict(self._data)

    @property
    def leaks(self) -> dict[str, MemoryInfo]:
        """Tests with delta below the leak threshold."""
        return {
            name: info
            for name, info in self._data.items()
            if info.delta < self._leak_threshold
        }

    def report(self) -> str:
        """Formatted memory leak summary. Empty string if no leaks."""
        leak_items = sorted(self.leaks.items(), key=lambda x: x[1].delta)
        if not leak_items:
            return ""
        lines = ["Memory Report:"]
        for name, info in leak_items:
            lines.append(
                f"  {name}: {info.delta:+d} bytes "
                f"(free: {info.free_before} -> {info.free_after})"
            )
        return "\n".join(lines)

    def reset(self) -> None:
        """Clear all tracked data."""
        self._current_test = ""
        self._data.clear()
