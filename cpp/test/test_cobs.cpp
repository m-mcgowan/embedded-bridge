#include <doctest/doctest.h>
#include <embedded_bridge/framing/cobs.h>

#include <cstring>
#include <string>
#include <vector>

using namespace ebridge;

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

TEST_SUITE("COBS Framer") {

TEST_CASE("decode single frame") {
    FrameCapture cap;
    CobsFramer<256> framer(FrameCapture::callback, &cap);

    RawCapture raw;
    CobsFramingWriter<256> writer(raw);
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
    CobsFramer<256> framer(FrameCapture::callback, &cap);

    RawCapture raw;
    CobsFramingWriter<256> writer(raw);
    writer.end_frame();

    for (auto b : raw.bytes) framer.process_byte(b);

    // COBS-encoded empty payload is just code=0x01 + delimiter 0x00
    // The decoder should deliver an empty payload
    REQUIRE(cap.frames.size() == 1);
    CHECK(cap.frames[0].empty());
}

TEST_CASE("multiple consecutive frames") {
    FrameCapture cap;
    CobsFramer<256> framer(FrameCapture::callback, &cap);

    RawCapture raw;
    CobsFramingWriter<256> writer(raw);
    writer.print("one");
    writer.end_frame();
    writer.print("two");
    writer.end_frame();

    for (auto b : raw.bytes) framer.process_byte(b);

    REQUIRE(cap.frames.size() == 2);
    CHECK(std::string(cap.frames[0].begin(), cap.frames[0].end()) == "one");
    CHECK(std::string(cap.frames[1].begin(), cap.frames[1].end()) == "two");
}

TEST_CASE("payload with zero bytes roundtrip") {
    FrameCapture cap;
    CobsFramer<256> framer(FrameCapture::callback, &cap);

    // Payload with embedded zeros
    const uint8_t payload[] = {0x00, 0x41, 0x00, 0x00, 0x42};

    RawCapture raw;
    CobsFramingWriter<256> writer(raw);
    writer.write(payload, sizeof(payload));
    writer.end_frame();

    for (auto b : raw.bytes) framer.process_byte(b);

    REQUIRE(cap.frames.size() == 1);
    REQUIRE(cap.frames[0].size() == sizeof(payload));
    CHECK(memcmp(cap.frames[0].data(), payload, sizeof(payload)) == 0);
}

TEST_CASE("payload of all zeros") {
    FrameCapture cap;
    CobsFramer<256> framer(FrameCapture::callback, &cap);

    const uint8_t payload[] = {0x00, 0x00, 0x00};

    RawCapture raw;
    CobsFramingWriter<256> writer(raw);
    writer.write(payload, sizeof(payload));
    writer.end_frame();

    for (auto b : raw.bytes) framer.process_byte(b);

    REQUIRE(cap.frames.size() == 1);
    REQUIRE(cap.frames[0].size() == 3);
    CHECK(cap.frames[0][0] == 0x00);
    CHECK(cap.frames[0][1] == 0x00);
    CHECK(cap.frames[0][2] == 0x00);
}

TEST_CASE("payload of 254 non-zero bytes") {
    FrameCapture cap;
    CobsFramer<256> framer(FrameCapture::callback, &cap);

    std::vector<uint8_t> payload(254, 0x41);  // 254 x 'A'

    RawCapture raw;
    CobsFramingWriter<256> writer(raw);
    writer.write(payload.data(), payload.size());
    writer.end_frame();

    for (auto b : raw.bytes) framer.process_byte(b);

    REQUIRE(cap.frames.size() == 1);
    CHECK(cap.frames[0] == payload);
}

TEST_CASE("buffer overflow recovers at next frame") {
    FrameCapture cap;
    CobsFramer<8> framer(FrameCapture::callback, &cap);  // tiny buffer

    // Overflow: send many bytes without delimiter
    for (int i = 0; i < 20; i++) framer.process_byte(0x41);
    framer.process_byte(0x00);  // delimiter

    // Next frame should work
    RawCapture raw;
    CobsFramingWriter<256> writer(raw);
    writer.write(static_cast<uint8_t>(0x42));
    writer.end_frame();
    for (auto b : raw.bytes) framer.process_byte(b);

    REQUIRE(cap.frames.size() == 1);
    CHECK(cap.frames[0][0] == 0x42);
}

TEST_CASE("discard drops buffered data") {
    RawCapture raw;
    CobsFramingWriter<256> writer(raw);
    writer.print("discard");
    CHECK(writer.buffered() == 7);
    writer.discard();
    CHECK(writer.buffered() == 0);
    CHECK(raw.bytes.empty());
}

TEST_CASE("overflow prevents emission") {
    RawCapture raw;
    CobsFramingWriter<4> writer(raw);
    writer.print("toolong");
    CHECK(writer.overflowed());
    writer.end_frame();
    CHECK(raw.bytes.empty());
}

TEST_CASE("delimiter byte (0x00) never appears in encoded output") {
    RawCapture raw;
    CobsFramingWriter<256> writer(raw);

    // Write various bytes including 0x00
    const uint8_t payload[] = {0x00, 0x01, 0x00, 0xFF, 0x00};
    writer.write(payload, sizeof(payload));
    writer.end_frame();

    // Check that 0x00 only appears as the final delimiter
    for (size_t i = 0; i < raw.bytes.size() - 1; i++) {
        CHECK(raw.bytes[i] != 0x00);
    }
    CHECK(raw.bytes.back() == 0x00);
}

}  // TEST_SUITE
