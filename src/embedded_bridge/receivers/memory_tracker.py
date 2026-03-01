"""Per-test heap memory tracking for embedded devices.

Parses ``[MEM]`` markers emitted by test frameworks that report heap
statistics before and after each test case. Identifies memory leaks
by tracking the delta between pre- and post-test heap sizes.

Wire format (from device)::

    [MEM] Before: free=<N>, min=<N>
    [MEM] After: free=<N> (delta=<+/-N>), min=<N>
    [MEM] WARNING: Test leaked ~<N> bytes!
"""

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class MemoryInfo:
    """Heap statistics for a single test case."""

    free_before: int = 0
    min_before: int = 0
    free_after: int = 0
    min_after: int = 0
    delta: int = 0


_MEM_BEFORE_RE = re.compile(r"\[MEM\] Before: free=(\d+), min=(\d+)")
_MEM_AFTER_RE = re.compile(
    r"\[MEM\] After: free=(\d+) \(delta=([+-]?\d+)\), min=(\d+)"
)


class MemoryTracker:
    """Receiver that tracks per-test heap memory usage.

    Call ``set_current_test()`` when a new test starts (e.g. from a
    ``>>> TEST START`` marker). The tracker pairs ``[MEM] Before`` and
    ``[MEM] After`` lines with the current test name.

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

        match = _MEM_BEFORE_RE.search(line)
        if match and self._current_test:
            info = self._data.setdefault(self._current_test, MemoryInfo())
            info.free_before = int(match.group(1))
            info.min_before = int(match.group(2))
            return

        match = _MEM_AFTER_RE.search(line)
        if match and self._current_test:
            info = self._data.setdefault(self._current_test, MemoryInfo())
            info.free_after = int(match.group(1))
            info.delta = int(match.group(2))
            info.min_after = int(match.group(3))
            return

    def set_current_test(self, name: str) -> None:
        """Set the test name for subsequent [MEM] lines."""
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
