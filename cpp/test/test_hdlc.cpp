#include <doctest/doctest.h>
#include <embedded_bridge/framing/hdlc.h>

#include <cstring>
#include <vector>

using namespace ebridge;
using namespace ebridge::hdlc;

namespace {

// Test helper: capture decoded frames
struct FrameCapture {
    std::vector<std::vector<uint8_t>> frames;

    static void callback(void* ctx, const uint8_t* data, size_t len) {
        auto* self = static_cast<FrameCapture*>(ctx);
        self->frames.emplace_back(data, data + len);
    }
};

// Test helper: capture raw bytes written by HdlcFramingWriter
class RawCapture : public Writer {
public:
    size_t write(uint8_t c) override {
        bytes.push_back(c);
        return 1;
    }
    std::vector<uint8_t> bytes;
};

}  // namespace

TEST_SUITE("HDLC Framer") {

TEST_CASE("decode single frame") {
    FrameCapture cap;
    HdlcFramer<256> framer(FrameCapture::callback, &cap);

    // Build a frame: FLAG + payload "Hi" + CRC + FLAG
    RawCapture raw;
    HdlcFramingWriter<256> writer(raw);
    writer.print("Hi");
    writer.end_frame();

    // Feed the encoded bytes into the framer
    for (auto b : raw.bytes) framer.process_byte(b);

    REQUIRE(cap.frames.size() == 1);
    CHECK(cap.frames[0].size() == 2);
    CHECK(cap.frames[0][0] == 'H');
    CHECK(cap.frames[0][1] == 'i');
}

TEST_CASE("decode empty payload") {
    FrameCapture cap;
    HdlcFramer<256> framer(FrameCapture::callback, &cap);

    RawCapture raw;
    HdlcFramingWriter<256> writer(raw);
    writer.end_frame();

    for (auto b : raw.bytes) framer.process_byte(b);

    REQUIRE(cap.frames.size() == 1);
    CHECK(cap.frames[0].empty());
}

TEST_CASE("corrupt CRC is rejected") {
    FrameCapture cap;
    HdlcFramer<256> framer(FrameCapture::callback, &cap);

    RawCapture raw;
    HdlcFramingWriter<256> writer(raw);
    writer.print("data");
    writer.end_frame();

    // Corrupt one byte in the middle (after opening FLAG)
    raw.bytes[2] ^= 0x01;

    for (auto b : raw.bytes) framer.process_byte(b);
    CHECK(cap.frames.empty());
}

TEST_CASE("multiple consecutive frames") {
    FrameCapture cap;
    HdlcFramer<256> framer(FrameCapture::callback, &cap);

    RawCapture raw;
    HdlcFramingWriter<256> writer(raw);
    writer.print("one");
    writer.end_frame();
    writer.print("two");
    writer.end_frame();

    for (auto b : raw.bytes) framer.process_byte(b);

    REQUIRE(cap.frames.size() == 2);
    CHECK(std::string(cap.frames[0].begin(), cap.frames[0].end()) == "one");
    CHECK(std::string(cap.frames[1].begin(), cap.frames[1].end()) == "two");
}

TEST_CASE("garbage before frame is ignored") {
    FrameCapture cap;
    HdlcFramer<256> framer(FrameCapture::callback, &cap);

    // Feed garbage first
    framer.process_byte(0xAA);
    framer.process_byte(0xBB);
    framer.process_byte(0xCC);

    // Then a valid frame
    RawCapture raw;
    HdlcFramingWriter<256> writer(raw);
    writer.print("ok");
    writer.end_frame();
    for (auto b : raw.bytes) framer.process_byte(b);

    REQUIRE(cap.frames.size() == 1);
    CHECK(std::string(cap.frames[0].begin(), cap.frames[0].end()) == "ok");
}

TEST_CASE("recovery after corrupt frame") {
    FrameCapture cap;
    HdlcFramer<256> framer(FrameCapture::callback, &cap);

    // Encode two frames
    RawCapture raw1, raw2;
    HdlcFramingWriter<256> w1(raw1), w2(raw2);
    w1.print("bad");
    w1.end_frame();
    w2.print("good");
    w2.end_frame();

    // Corrupt first frame
    raw1.bytes[2] ^= 0xFF;

    // Feed both
    for (auto b : raw1.bytes) framer.process_byte(b);
    for (auto b : raw2.bytes) framer.process_byte(b);

    // Only the second frame should be delivered
    REQUIRE(cap.frames.size() == 1);
    CHECK(std::string(cap.frames[0].begin(), cap.frames[0].end()) == "good");
}

TEST_CASE("byte stuffing roundtrip") {
    FrameCapture cap;
    HdlcFramer<256> framer(FrameCapture::callback, &cap);

    // Payload contains FLAG, ESC, XON, and XOFF bytes
    const uint8_t payload[] = {FLAG, ESC, XON, XOFF, 0x42};

    RawCapture raw;
    HdlcFramingWriter<256> writer(raw);
    writer.write(payload, sizeof(payload));
    writer.end_frame();

    for (auto b : raw.bytes) framer.process_byte(b);

    REQUIRE(cap.frames.size() == 1);
    REQUIRE(cap.frames[0].size() == sizeof(payload));
    CHECK(memcmp(cap.frames[0].data(), payload, sizeof(payload)) == 0);
}

TEST_CASE("flow control filters XON/XOFF") {
    FrameCapture cap;
    HdlcFramer<256> framer(FrameCapture::callback, &cap);
    framer.set_flow_control(true);

    RawCapture raw;
    HdlcFramingWriter<256> writer(raw);
    writer.print("ok");
    writer.end_frame();

    // Inject XON/XOFF around and within the frame bytes
    framer.process_byte(XON);
    for (auto b : raw.bytes) {
        framer.process_byte(b);
        framer.process_byte(XOFF);  // interleaved — should be filtered
    }

    REQUIRE(cap.frames.size() == 1);
    CHECK(std::string(cap.frames[0].begin(), cap.frames[0].end()) == "ok");
}

TEST_CASE("buffer overflow enters error state and recovers") {
    FrameCapture cap;
    HdlcFramer<8> framer(FrameCapture::callback, &cap);  // tiny buffer

    // Start a frame, overflow it
    framer.process_byte(FLAG);
    for (int i = 0; i < 20; i++) framer.process_byte(0x41);

    // New frame should still work
    RawCapture raw;
    HdlcFramingWriter<256> writer(raw);
    writer.write(static_cast<uint8_t>(0x42));
    writer.end_frame();
    for (auto b : raw.bytes) framer.process_byte(b);

    REQUIRE(cap.frames.size() == 1);
    CHECK(cap.frames[0][0] == 0x42);
}

TEST_CASE("reset clears state") {
    FrameCapture cap;
    HdlcFramer<256> framer(FrameCapture::callback, &cap);

    // Start a frame but don't finish
    framer.process_byte(FLAG);
    framer.process_byte(0x41);
    framer.reset();

    // After reset, needs FLAG to start again
    framer.process_byte(0x42);  // should be ignored (IDLE state)

    RawCapture raw;
    HdlcFramingWriter<256> writer(raw);
    writer.print("ok");
    writer.end_frame();
    for (auto b : raw.bytes) framer.process_byte(b);

    REQUIRE(cap.frames.size() == 1);
    CHECK(std::string(cap.frames[0].begin(), cap.frames[0].end()) == "ok");
}

}  // TEST_SUITE

TEST_SUITE("HDLC FramingWriter") {

TEST_CASE("frame structure: FLAG payload CRC FLAG") {
    RawCapture raw;
    HdlcFramingWriter<256> writer(raw);
    writer.write(static_cast<uint8_t>(0x41));  // 'A'
    writer.end_frame();

    // First byte: FLAG
    CHECK(raw.bytes.front() == FLAG);
    // Last byte: FLAG
    CHECK(raw.bytes.back() == FLAG);
    // Should have: FLAG + 'A' (no stuffing needed) + 2 CRC bytes (possibly stuffed) + FLAG
    CHECK(raw.bytes.size() >= 5);
}

TEST_CASE("FLAG and ESC in payload are escaped") {
    RawCapture raw;
    HdlcFramingWriter<256> writer(raw);
    writer.write(FLAG);
    writer.write(ESC);
    writer.end_frame();

    // Check no raw FLAG/ESC in the middle (between opening and closing FLAG)
    for (size_t i = 1; i < raw.bytes.size() - 1; i++) {
        if (raw.bytes[i] == ESC) {
            // Next byte should be the XORed version
            CHECK(i + 1 < raw.bytes.size() - 1);
            i++;  // skip the escaped byte
        } else {
            CHECK(raw.bytes[i] != FLAG);
        }
    }
}

TEST_CASE("discard drops buffered data") {
    RawCapture raw;
    HdlcFramingWriter<256> writer(raw);
    writer.print("discard this");
    CHECK(writer.buffered() == 12);
    writer.discard();
    CHECK(writer.buffered() == 0);
    CHECK(raw.bytes.empty());  // nothing emitted
}

TEST_CASE("overflow prevents emission") {
    RawCapture raw;
    HdlcFramingWriter<4> writer(raw);  // tiny buffer
    writer.print("toolong");
    CHECK(writer.overflowed());
    writer.end_frame();
    CHECK(raw.bytes.empty());  // nothing emitted on overflow
    CHECK(writer.buffered() == 0);  // buffer cleared
}

TEST_CASE("end_frame with no data emits valid empty frame") {
    RawCapture raw;
    HdlcFramingWriter<256> writer(raw);
    writer.end_frame();

    // Decode it
    FrameCapture cap;
    HdlcFramer<256> framer(FrameCapture::callback, &cap);
    for (auto b : raw.bytes) framer.process_byte(b);

    REQUIRE(cap.frames.size() == 1);
    CHECK(cap.frames[0].empty());
}

}  // TEST_SUITE
