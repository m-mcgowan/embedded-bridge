"""Tests for MemoryTracker receiver."""

from embedded_bridge.receivers.memory_tracker import MemoryInfo, MemoryTracker


class TestMemoryTracker:
    def test_before_and_after_paired(self):
        mt = MemoryTracker()
        mt.set_current_test("Suite/test_one")
        mt.feed("[MEM] Before: free=200000, min=180000")
        mt.feed("[MEM] After: free=199000 (delta=-1000), min=179000")

        info = mt.all_tests["Suite/test_one"]
        assert info.free_before == 200000
        assert info.min_before == 180000
        assert info.free_after == 199000
        assert info.delta == -1000
        assert info.min_after == 179000

    def test_no_test_name_ignores_lines(self):
        mt = MemoryTracker()
        mt.feed("[MEM] Before: free=200000, min=180000")
        assert mt.all_tests == {}

    def test_leaks_filters_by_threshold(self):
        mt = MemoryTracker(leak_threshold=-1000)
        mt.set_current_test("Suite/ok_test")
        mt.feed("[MEM] Before: free=200000, min=180000")
        mt.feed("[MEM] After: free=199500 (delta=-500), min=179500")

        mt.set_current_test("Suite/leaky_test")
        mt.feed("[MEM] Before: free=200000, min=180000")
        mt.feed("[MEM] After: free=195000 (delta=-5000), min=175000")

        assert "Suite/ok_test" not in mt.leaks
        assert "Suite/leaky_test" in mt.leaks
        assert mt.leaks["Suite/leaky_test"].delta == -5000

    def test_positive_delta_not_a_leak(self):
        mt = MemoryTracker()
        mt.set_current_test("Suite/growing")
        mt.feed("[MEM] Before: free=190000, min=180000")
        mt.feed("[MEM] After: free=195000 (delta=+5000), min=180000")
        assert mt.leaks == {}

    def test_report_empty_when_no_leaks(self):
        mt = MemoryTracker()
        assert mt.report() == ""

    def test_report_formats_leaks(self):
        mt = MemoryTracker()
        mt.set_current_test("Suite/leaky")
        mt.feed("[MEM] Before: free=200000, min=180000")
        mt.feed("[MEM] After: free=188000 (delta=-12000), min=170000")

        report = mt.report()
        assert "Memory Report:" in report
        assert "Suite/leaky" in report
        assert "-12000" in report

    def test_multiple_tests_tracked(self):
        mt = MemoryTracker()
        mt.set_current_test("A/one")
        mt.feed("[MEM] Before: free=200000, min=180000")
        mt.feed("[MEM] After: free=199000 (delta=-1000), min=179000")

        mt.set_current_test("A/two")
        mt.feed("[MEM] Before: free=199000, min=179000")
        mt.feed("[MEM] After: free=198000 (delta=-1000), min=178000")

        assert len(mt.all_tests) == 2
        assert mt.all_tests["A/one"].delta == -1000
        assert mt.all_tests["A/two"].delta == -1000

    def test_reset_clears_all(self):
        mt = MemoryTracker()
        mt.set_current_test("Suite/test")
        mt.feed("[MEM] Before: free=200000, min=180000")
        mt.reset()
        assert mt.all_tests == {}

    def test_bytes_input(self):
        mt = MemoryTracker()
        mt.set_current_test("Suite/test")
        mt.feed(b"[MEM] Before: free=200000, min=180000")
        assert mt.all_tests["Suite/test"].free_before == 200000

    def test_embedded_in_longer_line(self):
        mt = MemoryTracker()
        mt.set_current_test("Suite/test")
        mt.feed("some prefix [MEM] Before: free=200000, min=180000 suffix")
        assert mt.all_tests["Suite/test"].free_before == 200000
