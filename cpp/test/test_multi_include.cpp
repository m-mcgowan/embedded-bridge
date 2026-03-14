/// Test that all framing headers can be included together without
/// redefinition errors (FrameCallback was previously defined in each).
#include <doctest/doctest.h>

#include <embedded_bridge/framing/hdlc.h>
#include <embedded_bridge/framing/slip.h>
#include <embedded_bridge/framing/cobs.h>

using namespace ebridge;

TEST_SUITE("multi-include") {

TEST_CASE("FrameCallback is usable from all framers") {
    // This test primarily verifies compilation — if FrameCallback were
    // defined in each framing header, this file would fail to compile.
    FrameCallback cb = [](void*, const uint8_t*, size_t) {};

    HdlcFramer<64> hdlc(cb, nullptr);
    SlipFramer<64> slip(cb, nullptr);
    CobsFramer<64> cobs(cb, nullptr);

    CHECK(cb != nullptr);
    (void)hdlc;
    (void)slip;
    (void)cobs;
}

}  // TEST_SUITE
