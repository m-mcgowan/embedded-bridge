"""Tests for LineFramer."""

from embedded_bridge.framing.line import LineFramer


def test_single_line():
    f = LineFramer()
    f.feed(b"hello\n")
    assert f.drain() == ["hello"]


def test_multiple_lines_one_feed():
    f = LineFramer()
    f.feed(b"one\ntwo\nthree\n")
    assert f.drain() == ["one", "two", "three"]


def test_split_across_feeds():
    f = LineFramer()
    f.feed(b"hel")
    assert f.drain() == []
    f.feed(b"lo\n")
    assert f.drain() == ["hello"]


def test_crlf_normalization():
    f = LineFramer()
    f.feed(b"hello\r\n")
    assert f.drain() == ["hello"]


def test_incomplete_line_buffered():
    f = LineFramer()
    f.feed(b"partial")
    assert f.drain() == []
    f.feed(b" line\n")
    assert f.drain() == ["partial line"]


def test_utf8_multibyte_split():
    # U+00E9 = é = 0xC3 0xA9 in UTF-8
    f = LineFramer()
    f.feed(b"caf\xc3")
    assert f.drain() == []
    f.feed(b"\xa9\n")
    assert f.drain() == ["café"]


def test_empty_line():
    f = LineFramer()
    f.feed(b"\n")
    assert f.drain() == [""]


def test_multiple_empty_lines():
    f = LineFramer()
    f.feed(b"\n\n\n")
    assert f.drain() == ["", "", ""]


def test_drain_clears():
    f = LineFramer()
    f.feed(b"a\nb\n")
    assert f.drain() == ["a", "b"]
    assert f.drain() == []


def test_reset():
    f = LineFramer()
    f.feed(b"partial")
    f.reset()
    f.feed(b"fresh\n")
    assert f.drain() == ["fresh"]
