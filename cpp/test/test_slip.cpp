#include <doctest/doctest.h>
#include <embedded_bridge/framing/slip.h>

#include <cstring>
#include <string>
#include <vector>

using namespace ebridge;
using namespace ebridge::slip;

namespace {

struct FrameCapture {
    std::vector<std::vector<uint8_t>> frames;

    static void callback(void* ctx, const uint8_t* data, size_t len) {
        auto* self = static_cast<FrameCapture*>(ctx);
        self->frames.emplace_back(data, data + len);
    }
};

class RawCapture : public Writer {
public:
    size_t write(uint8_t c) override {
        bytes.push_back(c);
        return 1;
    }
    std::vector<uint8_t> bytes;
};

}  // namespace

TEST_SUITE("SLIP Framer") {

TEST_CASE("decode single frame") {
    FrameCapture cap;
    SlipFramer<256> framer(FrameCapture::callback, &cap);

    RawCapture raw;
    SlipFramingWriter<256> writer(raw);
    writer.print("Hi");
    writer.end_frame();

    for (auto b : raw.bytes) framer.process_byte(b);

    REQUIRE(cap.frames.size() == 1);
    CHECK(cap.frames[0].size() == 2);
    CHECK(cap.frames[0][0] == 'H');
    CHECK(cap.frames[0][1] == 'i');
}

TEST_CASE("decode empty payload") {
    FrameCapture cap;
    SlipFramer<256> framer(FrameCapture::callback, &cap);

    RawCapture raw;
    SlipFramingWriter<256> writer(raw);
    writer.end_frame();

    for (auto b : raw.bytes) framer.process_byte(b);

    // Empty frame (END-END) should not deliver (no payload)
    CHECK(cap.frames.empty());
}

TEST_CASE("multiple consecutive frames") {
    FrameCapture cap;
    SlipFramer<256> framer(FrameCapture::callback, &cap);

    RawCapture raw;
    SlipFramingWriter<256> writer(raw);
    writer.print("one");
    writer.end_frame();
    writer.print("two");
    writer.end_frame();

    for (auto b : raw.bytes) framer.process_byte(b);

    REQUIRE(cap.frames.size() == 2);
    CHECK(std::string(cap.frames[0].begin(), cap.frames[0].end()) == "one");
    CHECK(std::string(cap.frames[1].begin(), cap.frames[1].end()) == "two");
}

TEST_CASE("byte stuffing roundtrip") {
    FrameCapture cap;
    SlipFramer<256> framer(FrameCapture::callback, &cap);

    // Payload contains END and ESC bytes
    const uint8_t payload[] = {END, ESC, 0x42, END, ESC};

    RawCapture raw;
    SlipFramingWriter<256> writer(raw);
    writer.write(payload, sizeof(payload));
    writer.end_frame();

    for (auto b : raw.bytes) framer.process_byte(b);

    REQUIRE(cap.frames.size() == 1);
    REQUIRE(cap.frames[0].size() == sizeof(payload));
    CHECK(memcmp(cap.frames[0].data(), payload, sizeof(payload)) == 0);
}

TEST_CASE("garbage before frame is ignored") {
    FrameCapture cap;
    SlipFramer<256> framer(FrameCapture::callback, &cap);

    // Garbage bytes before the first END
    framer.process_byte(0xAA);
    framer.process_byte(0xBB);

    // Then a valid frame
    RawCapture raw;
    SlipFramingWriter<256> writer(raw);
    writer.print("ok");
    writer.end_frame();
    for (auto b : raw.bytes) framer.process_byte(b);

    // The garbage bytes before the first END form an unframed packet,
    // but the leading END from the writer flushes them. "ok" is delivered.
    bool found_ok = false;
    for (auto& f : cap.frames) {
        if (f.size() == 2 && f[0] == 'o' && f[1] == 'k') found_ok = true;
    }
    CHECK(found_ok);
}

TEST_CASE("buffer overflow recovers at next frame") {
    FrameCapture cap;
    SlipFramer<8> framer(FrameCapture::callback, &cap);  // tiny buffer

    // Overflow the buffer
    for (int i = 0; i < 20; i++) framer.process_byte(0x41);
    framer.process_byte(END);  // end the overflowed frame

    // Next frame should work
    RawCapture raw;
    SlipFramingWriter<256> writer(raw);
    writer.write(static_cast<uint8_t>(0x42));
    writer.end_frame();
    for (auto b : raw.bytes) framer.process_byte(b);

    REQUIRE(cap.frames.size() == 1);
    CHECK(cap.frames[0][0] == 0x42);
}

TEST_CASE("frame structure: END payload END") {
    RawCapture raw;
    SlipFramingWriter<256> writer(raw);
    writer.write(static_cast<uint8_t>(0x41));
    writer.end_frame();

    CHECK(raw.bytes.front() == END);
    CHECK(raw.bytes.back() == END);
}

TEST_CASE("discard drops buffered data") {
    RawCapture raw;
    SlipFramingWriter<256> writer(raw);
    writer.print("discard");
    CHECK(writer.buffered() == 7);
    writer.discard();
    CHECK(writer.buffered() == 0);
    CHECK(raw.bytes.empty());
}

TEST_CASE("overflow prevents emission") {
    RawCapture raw;
    SlipFramingWriter<4> writer(raw);
    writer.print("toolong");
    CHECK(writer.overflowed());
    writer.end_frame();
    CHECK(raw.bytes.empty());
}

}  // TEST_SUITE
