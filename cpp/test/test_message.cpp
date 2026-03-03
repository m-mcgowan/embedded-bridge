#include <doctest/doctest.h>
#include <embedded_bridge/message.h>

#include <cstring>
#include <string>
#include <vector>

using namespace ebridge;

namespace {

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

/// Capture raw bytes from a Writer (used for MessageWriter tests).
class RawCapture : public Writer {
public:
    size_t write(uint8_t c) override {
        bytes.push_back(c);
        return 1;
    }
    std::vector<uint8_t> bytes;
    void clear() { bytes.clear(); }
};

/// Build a binary message on the wire: SOH + version + varint(len) + payload.
std::vector<uint8_t> encode_bin(const uint8_t* payload, size_t len) {
    std::vector<uint8_t> out;
    out.push_back(SOH);
    out.push_back(BINARY_PROTOCOL_V1);
    uint8_t varint_buf[VARINT_MAX_BYTES];
    size_t vlen = encode_varint(static_cast<uint32_t>(len), varint_buf);
    out.insert(out.end(), varint_buf, varint_buf + vlen);
    out.insert(out.end(), payload, payload + len);
    return out;
}

std::vector<uint8_t> encode_bin(const char* s) {
    return encode_bin(reinterpret_cast<const uint8_t*>(s), strlen(s));
}

std::vector<uint8_t> encode_bin(const std::vector<uint8_t>& payload) {
    return encode_bin(payload.data(), payload.size());
}

/// Buffering handler that collects complete messages for assertions.
struct Capture : public BufferingMessageHandler<512, 4096> {
    std::vector<std::string> texts;
    std::vector<std::vector<uint8_t>> binaries;

    void on_text(const char* data, size_t len) override {
        texts.emplace_back(data, len);
    }

    void on_binary(const uint8_t* data, size_t len) override {
        binaries.emplace_back(data, data + len);
    }
};

/// Streaming handler that records events.
struct StreamCapture : public MessageHandler {
    struct Event {
        std::string type;
        std::string sdata;
        std::vector<uint8_t> bdata;
        uint32_t length = 0;
    };
    std::vector<Event> events;

    void on_text_data(const char* data, size_t len) override {
        events.push_back({"text_data", std::string(data, len), {}, 0});
    }
    void on_text_end() override {
        events.push_back({"text_end", "", {}, 0});
    }
    void on_binary_start(uint32_t length) override {
        events.push_back({"binary_start", "", {}, length});
    }
    void on_binary_data(const uint8_t* data, size_t len) override {
        events.push_back({"binary_data", "", std::vector<uint8_t>(data, data + len), 0});
    }
    void on_binary_end() override {
        events.push_back({"binary_end", "", {}, 0});
    }
};

void feed_bytes(MessageReader& reader, const std::vector<uint8_t>& data) {
    reader.feed(data.data(), data.size());
}

void feed_string(MessageReader& reader, const char* s) {
    reader.feed(reinterpret_cast<const uint8_t*>(s), strlen(s));
}

}  // namespace

// ---------------------------------------------------------------------------
// MessageReader — buffered handler tests
// ---------------------------------------------------------------------------

TEST_SUITE("MessageReader") {

TEST_CASE("text lines") {
    Capture cap;
    MessageReader reader(cap);
    feed_string(reader, "hello\nworld\n");
    REQUIRE(cap.texts.size() == 2);
    CHECK(cap.texts[0] == "hello");
    CHECK(cap.texts[1] == "world");
}

TEST_CASE("CRLF normalization") {
    Capture cap;
    MessageReader reader(cap);
    feed_string(reader, "hello\r\nworld\r\n");
    REQUIRE(cap.texts.size() == 2);
    CHECK(cap.texts[0] == "hello");
    CHECK(cap.texts[1] == "world");
}

TEST_CASE("empty text line") {
    Capture cap;
    MessageReader reader(cap);
    feed_string(reader, "\n");
    REQUIRE(cap.texts.size() == 1);
    CHECK(cap.texts[0].empty());
}

TEST_CASE("binary message") {
    Capture cap;
    MessageReader reader(cap);
    auto wire = encode_bin("payload");
    feed_bytes(reader, wire);
    REQUIRE(cap.binaries.size() == 1);
    CHECK(std::string(cap.binaries[0].begin(), cap.binaries[0].end()) == "payload");
}

TEST_CASE("empty binary") {
    Capture cap;
    MessageReader reader(cap);
    auto wire = encode_bin(std::vector<uint8_t>{});
    feed_bytes(reader, wire);
    REQUIRE(cap.binaries.size() == 1);
    CHECK(cap.binaries[0].empty());
}

TEST_CASE("text binary interleaved") {
    Capture cap;
    MessageReader reader(cap);
    std::vector<uint8_t> wire;
    // "line1\n"
    const char* l1 = "line1\n";
    wire.insert(wire.end(), l1, l1 + 6);
    // binary "bin1"
    auto b1 = encode_bin("bin1");
    wire.insert(wire.end(), b1.begin(), b1.end());
    // "line2\n"
    const char* l2 = "line2\n";
    wire.insert(wire.end(), l2, l2 + 6);

    feed_bytes(reader, wire);

    REQUIRE(cap.texts.size() == 2);
    CHECK(cap.texts[0] == "line1");
    CHECK(cap.texts[1] == "line2");
    REQUIRE(cap.binaries.size() == 1);
    CHECK(std::string(cap.binaries[0].begin(), cap.binaries[0].end()) == "bin1");
}

TEST_CASE("multiple binary") {
    Capture cap;
    MessageReader reader(cap);
    auto b1 = encode_bin("a");
    auto b2 = encode_bin("b");
    std::vector<uint8_t> wire;
    wire.insert(wire.end(), b1.begin(), b1.end());
    wire.insert(wire.end(), b2.begin(), b2.end());
    feed_bytes(reader, wire);
    REQUIRE(cap.binaries.size() == 2);
    CHECK(std::string(cap.binaries[0].begin(), cap.binaries[0].end()) == "a");
    CHECK(std::string(cap.binaries[1].begin(), cap.binaries[1].end()) == "b");
}

TEST_CASE("split across feeds") {
    Capture cap;
    MessageReader reader(cap);
    auto wire = encode_bin("chunked");
    size_t mid = wire.size() / 2;
    reader.feed(wire.data(), mid);
    CHECK(cap.binaries.empty());
    reader.feed(wire.data() + mid, wire.size() - mid);
    REQUIRE(cap.binaries.size() == 1);
    CHECK(std::string(cap.binaries[0].begin(), cap.binaries[0].end()) == "chunked");
}

TEST_CASE("text split across feeds") {
    Capture cap;
    MessageReader reader(cap);
    feed_string(reader, "hel");
    CHECK(cap.texts.empty());
    feed_string(reader, "lo\n");
    REQUIRE(cap.texts.size() == 1);
    CHECK(cap.texts[0] == "hello");
}

TEST_CASE("byte at a time") {
    Capture cap;
    MessageReader reader(cap);
    std::vector<uint8_t> wire;
    const char* t = "text\n";
    wire.insert(wire.end(), t, t + 5);
    auto bin = encode_bin("bin");
    wire.insert(wire.end(), bin.begin(), bin.end());
    const char* m = "more\n";
    wire.insert(wire.end(), m, m + 5);

    for (auto b : wire) {
        reader.feed(&b, 1);
    }
    REQUIRE(cap.texts.size() == 2);
    CHECK(cap.texts[0] == "text");
    CHECK(cap.texts[1] == "more");
    REQUIRE(cap.binaries.size() == 1);
    CHECK(std::string(cap.binaries[0].begin(), cap.binaries[0].end()) == "bin");
}

TEST_CASE("large payload") {
    Capture cap;
    MessageReader reader(cap);
    std::vector<uint8_t> payload(4000, 0x58);  // 'X' * 4000
    auto wire = encode_bin(payload);
    feed_bytes(reader, wire);
    REQUIRE(cap.binaries.size() == 1);
    CHECK(cap.binaries[0] == payload);
}

TEST_CASE("payload containing SOH") {
    Capture cap;
    MessageReader reader(cap);
    std::vector<uint8_t> payload = {SOH, SOH, 0x42};
    auto wire = encode_bin(payload);
    feed_bytes(reader, wire);
    REQUIRE(cap.binaries.size() == 1);
    CHECK(cap.binaries[0] == payload);
}

TEST_CASE("payload containing newlines") {
    Capture cap;
    MessageReader reader(cap);
    std::vector<uint8_t> payload = {'a', '\n', 'b', '\n'};
    auto wire = encode_bin(payload);
    feed_bytes(reader, wire);
    REQUIRE(cap.binaries.size() == 1);
    CHECK(cap.binaries[0] == payload);
}

TEST_CASE("all byte values") {
    Capture cap;
    MessageReader reader(cap);
    std::vector<uint8_t> payload(256);
    for (int i = 0; i < 256; i++) payload[i] = static_cast<uint8_t>(i);
    auto wire = encode_bin(payload);
    feed_bytes(reader, wire);
    REQUIRE(cap.binaries.size() == 1);
    CHECK(cap.binaries[0] == payload);
}

TEST_CASE("unknown version recovery") {
    Capture cap;
    MessageReader reader(cap);
    // SOH + unknown version 0xFF + "text\n"
    std::vector<uint8_t> wire = {SOH, 0xFF};
    const char* t = "text\n";
    wire.insert(wire.end(), t, t + 5);
    feed_bytes(reader, wire);
    REQUIRE(cap.texts.size() == 1);
    CHECK(cap.texts[0] == "text");
}

TEST_CASE("reset clears state") {
    Capture cap;
    MessageReader reader(cap);
    uint8_t soh = SOH;
    reader.feed(&soh, 1);
    reader.reset();
    feed_string(reader, "fresh\n");
    REQUIRE(cap.texts.size() == 1);
    CHECK(cap.texts[0] == "fresh");
}

TEST_CASE("SOH terminates text") {
    Capture cap;
    MessageReader reader(cap);
    std::vector<uint8_t> wire;
    const char* p = "partial";
    wire.insert(wire.end(), p, p + 7);
    auto bin = encode_bin("bin");
    wire.insert(wire.end(), bin.begin(), bin.end());
    feed_bytes(reader, wire);
    // "partial" emitted as text (SOH acts as implicit line end)
    REQUIRE(cap.texts.size() == 1);
    CHECK(cap.texts[0] == "partial");
    REQUIRE(cap.binaries.size() == 1);
    CHECK(std::string(cap.binaries[0].begin(), cap.binaries[0].end()) == "bin");
}

}  // TEST_SUITE

// ---------------------------------------------------------------------------
// Streaming handler tests
// ---------------------------------------------------------------------------

TEST_SUITE("MessageReader Streaming") {

TEST_CASE("text streaming") {
    StreamCapture cap;
    MessageReader reader(cap);
    feed_string(reader, "hel");
    feed_string(reader, "lo\n");
    REQUIRE(cap.events.size() == 3);
    CHECK(cap.events[0].type == "text_data");
    CHECK(cap.events[0].sdata == "hel");
    CHECK(cap.events[1].type == "text_data");
    CHECK(cap.events[1].sdata == "lo");
    CHECK(cap.events[2].type == "text_end");
}

TEST_CASE("binary streaming") {
    StreamCapture cap;
    MessageReader reader(cap);
    auto wire = encode_bin("ABCDEF");
    size_t mid = wire.size() - 3;
    reader.feed(wire.data(), mid);
    reader.feed(wire.data() + mid, wire.size() - mid);
    CHECK(cap.events.front().type == "binary_start");
    CHECK(cap.events.front().length == 6);
    CHECK(cap.events.back().type == "binary_end");
    // Reassemble data chunks
    std::vector<uint8_t> assembled;
    for (auto& e : cap.events) {
        if (e.type == "binary_data") {
            assembled.insert(assembled.end(), e.bdata.begin(), e.bdata.end());
        }
    }
    CHECK(std::string(assembled.begin(), assembled.end()) == "ABCDEF");
}

TEST_CASE("empty binary streaming") {
    StreamCapture cap;
    MessageReader reader(cap);
    auto wire = encode_bin(std::vector<uint8_t>{});
    feed_bytes(reader, wire);
    REQUIRE(cap.events.size() == 2);
    CHECK(cap.events[0].type == "binary_start");
    CHECK(cap.events[0].length == 0);
    CHECK(cap.events[1].type == "binary_end");
}

TEST_CASE("interleaved streaming") {
    StreamCapture cap;
    MessageReader reader(cap);
    std::vector<uint8_t> wire;
    const char* h = "hi\n";
    wire.insert(wire.end(), h, h + 3);
    auto bin = encode_bin("AB");
    wire.insert(wire.end(), bin.begin(), bin.end());
    const char* b = "bye\n";
    wire.insert(wire.end(), b, b + 4);
    feed_bytes(reader, wire);

    REQUIRE(cap.events.size() == 7);
    CHECK(cap.events[0].type == "text_data");
    CHECK(cap.events[0].sdata == "hi");
    CHECK(cap.events[1].type == "text_end");
    CHECK(cap.events[2].type == "binary_start");
    CHECK(cap.events[2].length == 2);
    CHECK(cap.events[3].type == "binary_data");
    CHECK(cap.events[4].type == "binary_end");
    CHECK(cap.events[5].type == "text_data");
    CHECK(cap.events[5].sdata == "bye");
    CHECK(cap.events[6].type == "text_end");
}

}  // TEST_SUITE

// ---------------------------------------------------------------------------
// MessageWriter tests
// ---------------------------------------------------------------------------

TEST_SUITE("MessageWriter") {

TEST_CASE("write_text") {
    RawCapture raw;
    MessageWriter writer(raw);
    writer.write_text("hello");
    CHECK(raw.bytes.size() == 6);
    CHECK(raw.bytes[0] == 'h');
    CHECK(raw.bytes[4] == 'o');
    CHECK(raw.bytes[5] == '\n');
}

TEST_CASE("write_binary") {
    RawCapture raw;
    MessageWriter writer(raw);
    const uint8_t payload[] = {0x41, 0x42, 0x43};
    writer.write_binary(payload, 3);
    CHECK(raw.bytes[0] == SOH);
    CHECK(raw.bytes[1] == BINARY_PROTOCOL_V1);
    // Varint 3 = single byte 0x03
    CHECK(raw.bytes[2] == 0x03);
    CHECK(raw.bytes[3] == 0x41);
    CHECK(raw.bytes[4] == 0x42);
    CHECK(raw.bytes[5] == 0x43);
    CHECK(raw.bytes.size() == 6);
}

TEST_CASE("roundtrip") {
    RawCapture raw;
    MessageWriter writer(raw);
    writer.write_text("hello");
    const uint8_t payload[] = "world";
    writer.write_binary(payload, 5);

    Capture cap;
    MessageReader reader(cap);
    feed_bytes(reader, raw.bytes);

    REQUIRE(cap.texts.size() == 1);
    CHECK(cap.texts[0] == "hello");
    REQUIRE(cap.binaries.size() == 1);
    CHECK(std::string(cap.binaries[0].begin(), cap.binaries[0].end()) == "world");
}

TEST_CASE("streaming text") {
    RawCapture raw;
    MessageWriter writer(raw);
    writer.begin_text();
    writer.write("hel", 3);
    writer.write("lo", 2);
    writer.end_text();

    Capture cap;
    MessageReader reader(cap);
    feed_bytes(reader, raw.bytes);
    REQUIRE(cap.texts.size() == 1);
    CHECK(cap.texts[0] == "hello");
}

TEST_CASE("streaming binary") {
    RawCapture raw;
    MessageWriter writer(raw);
    writer.begin_binary(6);
    const uint8_t d1[] = "ABC";
    writer.write(d1, 3);
    const uint8_t d2[] = "DEF";
    writer.write(d2, 3);
    writer.end_binary();

    Capture cap;
    MessageReader reader(cap);
    feed_bytes(reader, raw.bytes);
    REQUIRE(cap.binaries.size() == 1);
    CHECK(std::string(cap.binaries[0].begin(), cap.binaries[0].end()) == "ABCDEF");
}

TEST_CASE("streaming roundtrip") {
    RawCapture raw;
    MessageWriter writer(raw);

    writer.begin_text();
    writer.write("line", 4);
    writer.end_text();

    writer.begin_binary(3);
    const uint8_t d1[] = "AB";
    writer.write(d1, 2);
    const uint8_t d2[] = "C";
    writer.write(d2, 1);
    writer.end_binary();

    Capture cap;
    MessageReader reader(cap);
    feed_bytes(reader, raw.bytes);
    REQUIRE(cap.texts.size() == 1);
    CHECK(cap.texts[0] == "line");
    REQUIRE(cap.binaries.size() == 1);
    CHECK(std::string(cap.binaries[0].begin(), cap.binaries[0].end()) == "ABC");
}

}  // TEST_SUITE

// ---------------------------------------------------------------------------
// Varint tests
// ---------------------------------------------------------------------------

TEST_SUITE("Varint") {

TEST_CASE("roundtrip") {
    uint32_t values[] = {0, 1, 127, 128, 255, 300, 16383, 16384, 65535, 1000000};
    for (auto value : values) {
        uint8_t buf[VARINT_MAX_BYTES];
        size_t encoded_len = encode_varint(value, buf);
        uint32_t decoded;
        size_t consumed;
        REQUIRE(decode_varint(buf, encoded_len, decoded, consumed));
        CHECK(decoded == value);
        CHECK(consumed == encoded_len);
    }
}

TEST_CASE("encoding sizes") {
    uint8_t buf[VARINT_MAX_BYTES];
    CHECK(encode_varint(0, buf) == 1);
    CHECK(encode_varint(127, buf) == 1);
    CHECK(encode_varint(128, buf) == 2);
    CHECK(encode_varint(16383, buf) == 2);
    CHECK(encode_varint(16384, buf) == 3);
}

}  // TEST_SUITE
