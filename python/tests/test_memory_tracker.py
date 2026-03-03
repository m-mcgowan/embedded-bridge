"""Tests for MemoryTracker receiver."""

from embedded_bridge.receivers.memory_tracker import MemoryInfo, MemoryTracker

try:
    from pio_test_runner.protocol import format_crc
except ImportError:
    # Standalone: compute CRC inline (mirrors memory_tracker.py fallback)
    def format_crc(content: str) -> str:
        crc = 0x00
        for byte in content.encode("utf-8"):
            crc ^= byte
            for _ in range(8):
                if crc & 0x80:
                    crc = ((crc << 1) ^ 0x31) & 0xFF
                else:
                    crc = (crc << 1) & 0xFF
        return f"{content} *{crc:02X}"


def _crc(content: str) -> str:
    return format_crc(content)


class TestMemoryTracker:
    def test_before_and_after_paired(self):
        mt = MemoryTracker()
        mt.set_current_test("Suite/test_one")
        mt.feed(_crc("PTR:MEM:BEFORE free=200000 min=180000"))
        mt.feed(_crc("PTR:MEM:AFTER free=199000 delta=-1000 min=179000"))

        info = mt.all_tests["Suite/test_one"]
        assert info.free_before == 200000
        assert info.min_before == 180000
        assert info.free_after == 199000
        assert info.delta == -1000
        assert info.min_after == 179000

    def test_no_test_name_ignores_lines(self):
        mt = MemoryTracker()
        mt.feed(_crc("PTR:MEM:BEFORE free=200000 min=180000"))
        assert mt.all_tests == {}

    def test_leaks_filters_by_threshold(self):
        mt = MemoryTracker(leak_threshold=-1000)
        mt.set_current_test("Suite/ok_test")
        mt.feed(_crc("PTR:MEM:BEFORE free=200000 min=180000"))
        mt.feed(_crc("PTR:MEM:AFTER free=199500 delta=-500 min=179500"))

        mt.set_current_test("Suite/leaky_test")
        mt.feed(_crc("PTR:MEM:BEFORE free=200000 min=180000"))
        mt.feed(_crc("PTR:MEM:AFTER free=195000 delta=-5000 min=175000"))

        assert "Suite/ok_test" not in mt.leaks
        assert "Suite/leaky_test" in mt.leaks
        assert mt.leaks["Suite/leaky_test"].delta == -5000

    def test_positive_delta_not_a_leak(self):
        mt = MemoryTracker()
        mt.set_current_test("Suite/growing")
        mt.feed(_crc("PTR:MEM:BEFORE free=190000 min=180000"))
        mt.feed(_crc("PTR:MEM:AFTER free=195000 delta=5000 min=180000"))
        assert mt.leaks == {}

    def test_report_empty_when_no_leaks(self):
        mt = MemoryTracker()
        assert mt.report() == ""

    def test_report_formats_leaks(self):
        mt = MemoryTracker()
        mt.set_current_test("Suite/leaky")
        mt.feed(_crc("PTR:MEM:BEFORE free=200000 min=180000"))
        mt.feed(_crc("PTR:MEM:AFTER free=188000 delta=-12000 min=170000"))

        report = mt.report()
        assert "Memory Report:" in report
        assert "Suite/leaky" in report
        assert "-12000" in report

    def test_multiple_tests_tracked(self):
        mt = MemoryTracker()
        mt.set_current_test("A/one")
        mt.feed(_crc("PTR:MEM:BEFORE free=200000 min=180000"))
        mt.feed(_crc("PTR:MEM:AFTER free=199000 delta=-1000 min=179000"))

        mt.set_current_test("A/two")
        mt.feed(_crc("PTR:MEM:BEFORE free=199000 min=179000"))
        mt.feed(_crc("PTR:MEM:AFTER free=198000 delta=-1000 min=178000"))

        assert len(mt.all_tests) == 2
        assert mt.all_tests["A/one"].delta == -1000
        assert mt.all_tests["A/two"].delta == -1000

    def test_reset_clears_all(self):
        mt = MemoryTracker()
        mt.set_current_test("Suite/test")
        mt.feed(_crc("PTR:MEM:BEFORE free=200000 min=180000"))
        mt.reset()
        assert mt.all_tests == {}

    def test_bytes_input(self):
        mt = MemoryTracker()
        mt.set_current_test("Suite/test")
        mt.feed(_crc("PTR:MEM:BEFORE free=200000 min=180000").encode())
        assert mt.all_tests["Suite/test"].free_before == 200000

    def test_no_crc_accepted(self):
        mt = MemoryTracker()
        mt.set_current_test("Suite/test")
        mt.feed("PTR:MEM:BEFORE free=200000 min=180000")
        assert mt.all_tests["Suite/test"].free_before == 200000

    def test_invalid_crc_rejected(self):
        mt = MemoryTracker()
        mt.set_current_test("Suite/test")
        mt.feed("PTR:MEM:BEFORE free=200000 min=180000 *00")
        assert mt.all_tests == {}
