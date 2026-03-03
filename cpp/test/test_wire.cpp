#include <doctest/doctest.h>
#include <nlohmann/json.hpp>

#include <embedded_bridge/detail/crc16.h>
#include <embedded_bridge/framing/hdlc.h>
#include <embedded_bridge/framing/slip.h>
#include <embedded_bridge/framing/cobs.h>
#include <embedded_bridge/message.h>

#include <fstream>
#include <string>
#include <vector>

using json = nlohmann::json;
using namespace ebridge;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

static std::vector<uint8_t> hex_to_bytes(const std::string& hex) {
    std::vector<uint8_t> out;
    for (size_t i = 0; i + 1 < hex.size(); i += 2) {
        out.push_back(static_cast<uint8_t>(
            std::stoul(hex.substr(i, 2), nullptr, 16)));
    }
    return out;
}

static std::string bytes_to_hex(const uint8_t* data, size_t len) {
    static const char digits[] = "0123456789abcdef";
    std::string out;
    out.reserve(len * 2);
    for (size_t i = 0; i < len; i++) {
        out.push_back(digits[data[i] >> 4]);
        out.push_back(digits[data[i] & 0x0F]);
    }
    return out;
}

static json load_vectors(const char* filename) {
    std::string path = std::string(WIRE_TESTS_DIR) + "/" + filename;
    std::ifstream f(path);
    REQUIRE(f.good());
    json j;
    f >> j;
    return j;
}

/// Helper to get raw bytes from a BufferWriter (which stores as char).
static std::string writer_to_hex(const BufferWriter<256>& w) {
    return bytes_to_hex(reinterpret_cast<const uint8_t*>(w.str()), w.len());
}

/// Feed a byte vector into a framer one byte at a time.
template <typename Framer>
static void feed_bytes(Framer& framer, const std::vector<uint8_t>& data) {
    for (uint8_t b : data) {
        framer.process_byte(b);
    }
}

/// Callback context for framers.
struct FrameCapture {
    std::vector<uint8_t> last_frame;
    int frame_count = 0;

    static void callback(void* ctx, const uint8_t* data, size_t len) {
        auto* self = static_cast<FrameCapture*>(ctx);
        self->last_frame.assign(data, data + len);
        self->frame_count++;
    }
};

// ---------------------------------------------------------------------------
// CRC-16
// ---------------------------------------------------------------------------

TEST_CASE("wire: crc16") {
    auto j = load_vectors("crc16.json");

    SUBCASE("vectors") {
        for (const auto& v : j["vectors"]) {
            CAPTURE(v["name"].get<std::string>());
            auto input = hex_to_bytes(v["input_hex"].get<std::string>());
            uint16_t expected = v["crc"].get<uint16_t>();
            uint16_t actual = detail::crc16_hdlc(input.data(), input.size());
            CHECK(actual == expected);
        }
    }

    SUBCASE("residue") {
        for (const auto& v : j["residue_vectors"]) {
            CAPTURE(v["name"].get<std::string>());
            auto frame = hex_to_bytes(v["frame_hex"].get<std::string>());
            uint16_t expected = v["frame_crc"].get<uint16_t>();
            uint16_t actual = detail::crc16_hdlc(frame.data(), frame.size());
            CHECK(actual == expected);
            CHECK(actual == j["good_residue"].get<uint16_t>());
        }
    }
}

// ---------------------------------------------------------------------------
// HDLC
// ---------------------------------------------------------------------------

TEST_CASE("wire: hdlc encode/decode") {
    auto j = load_vectors("hdlc.json");

    for (const auto& v : j["encode_vectors"]) {
        CAPTURE(v["name"].get<std::string>());
        auto payload = hex_to_bytes(v["payload_hex"].get<std::string>());

        // Encode
        BufferWriter<256> buf;
        HdlcFramingWriter<256> writer(buf);
        writer.write(payload.data(), payload.size());
        writer.end_frame();
        CHECK(writer_to_hex(buf) == v["encoded_hex"].get<std::string>());

        // Decode (roundtrip)
        auto wire = hex_to_bytes(v["encoded_hex"].get<std::string>());
        FrameCapture cap;
        HdlcFramer<256> framer(FrameCapture::callback, &cap);
        feed_bytes(framer, wire);
        CHECK(cap.frame_count == 1);
        CHECK(cap.last_frame == payload);
    }
}

TEST_CASE("wire: hdlc decode errors") {
    auto j = load_vectors("hdlc.json");

    for (const auto& v : j["decode_error_vectors"]) {
        CAPTURE(v["name"].get<std::string>());
        auto wire = hex_to_bytes(v["wire_hex"].get<std::string>());

        FrameCapture cap;
        HdlcFramer<256> framer(FrameCapture::callback, &cap);
        feed_bytes(framer, wire);
        CHECK(cap.frame_count == 0);
    }
}

// ---------------------------------------------------------------------------
// SLIP
// ---------------------------------------------------------------------------

TEST_CASE("wire: slip encode/decode") {
    auto j = load_vectors("slip.json");

    for (const auto& v : j["encode_vectors"]) {
        CAPTURE(v["name"].get<std::string>());
        auto payload = hex_to_bytes(v["payload_hex"].get<std::string>());

        // Encode
        BufferWriter<256> buf;
        SlipFramingWriter<256> writer(buf);
        writer.write(payload.data(), payload.size());
        writer.end_frame();
        CHECK(writer_to_hex(buf) == v["encoded_hex"].get<std::string>());

        // Decode (roundtrip)
        auto wire = hex_to_bytes(v["encoded_hex"].get<std::string>());
        FrameCapture cap;
        SlipFramer<256> framer(FrameCapture::callback, &cap);
        feed_bytes(framer, wire);
        CHECK(cap.frame_count == 1);
        CHECK(cap.last_frame == payload);
    }
}

// ---------------------------------------------------------------------------
// COBS
// ---------------------------------------------------------------------------

TEST_CASE("wire: cobs encode/decode") {
    auto j = load_vectors("cobs.json");

    for (const auto& v : j["encode_vectors"]) {
        CAPTURE(v["name"].get<std::string>());
        auto payload = hex_to_bytes(v["payload_hex"].get<std::string>());

        // Encode (framed = cobs + delimiter)
        BufferWriter<256> buf;
        CobsFramingWriter<256> writer(buf);
        writer.write(payload.data(), payload.size());
        writer.end_frame();
        CHECK(writer_to_hex(buf) == v["framed_hex"].get<std::string>());

        // Decode (roundtrip)
        auto wire = hex_to_bytes(v["framed_hex"].get<std::string>());
        FrameCapture cap;
        CobsFramer<256> framer(FrameCapture::callback, &cap);
        feed_bytes(framer, wire);
        CHECK(cap.frame_count == 1);
        CHECK(cap.last_frame == payload);
    }
}

// ---------------------------------------------------------------------------
// Message protocol — varint
// ---------------------------------------------------------------------------

TEST_CASE("wire: varint encode/decode") {
    auto j = load_vectors("message.json");

    for (const auto& v : j["varint_vectors"]) {
        uint32_t value = v["value"].get<uint32_t>();
        auto expected = hex_to_bytes(v["encoded_hex"].get<std::string>());
        size_t expected_bytes = v["bytes"].get<size_t>();
        CAPTURE(value);

        // Encode
        uint8_t buf[5];
        size_t n = encode_varint(value, buf);
        CHECK(n == expected_bytes);
        CHECK(bytes_to_hex(buf, n) == v["encoded_hex"].get<std::string>());

        // Decode
        uint32_t decoded_value = 0;
        size_t consumed = 0;
        bool ok = decode_varint(expected.data(), expected.size(),
                                decoded_value, consumed);
        CHECK(ok);
        CHECK(decoded_value == value);
        CHECK(consumed == expected_bytes);
    }
}

// ---------------------------------------------------------------------------
// Message protocol — writer output
// ---------------------------------------------------------------------------

TEST_CASE("wire: message writer text") {
    auto j = load_vectors("message.json");

    for (const auto& v : j["text_vectors"]) {
        CAPTURE(v["name"].get<std::string>());
        std::string text = v["text"].get<std::string>();

        BufferWriter<256> buf;
        MessageWriter writer(buf);
        writer.write_text(text.c_str(), text.size());
        CHECK(writer_to_hex(buf) == v["wire_hex"].get<std::string>());
    }
}

TEST_CASE("wire: message writer binary") {
    auto j = load_vectors("message.json");

    for (const auto& v : j["binary_vectors"]) {
        CAPTURE(v["name"].get<std::string>());
        auto payload = hex_to_bytes(v["payload_hex"].get<std::string>());

        BufferWriter<256> buf;
        MessageWriter writer(buf);
        writer.write_binary(payload.data(), payload.size());
        CHECK(writer_to_hex(buf) == v["wire_hex"].get<std::string>());
    }
}

// ---------------------------------------------------------------------------
// Message protocol — reader
// ---------------------------------------------------------------------------

TEST_CASE("wire: message reader") {
    auto j = load_vectors("message.json");

    struct Result {
        std::string type;
        std::string value;
    };

    for (const auto& v : j["reader_vectors"]) {
        CAPTURE(v["name"].get<std::string>());
        auto wire = hex_to_bytes(v["wire_hex"].get<std::string>());

        std::vector<Result> results;

        class Capture : public BufferingMessageHandler<512, 4096> {
        public:
            std::vector<Result>& results;
            explicit Capture(std::vector<Result>& r) : results(r) {}
            void on_text(const char* data, size_t len) override {
                if (len > 0 && data[len - 1] == '\r') len--;
                results.push_back({"text", std::string(data, len)});
            }
            void on_binary(const uint8_t* data, size_t len) override {
                results.push_back({"binary", bytes_to_hex(data, len)});
            }
        };

        Capture handler(results);
        MessageReader reader(handler);
        reader.feed(wire.data(), wire.size());

        const auto& expected = v["expected"];
        REQUIRE(results.size() == expected.size());
        for (size_t i = 0; i < results.size(); i++) {
            CHECK(results[i].type == expected[i]["type"].get<std::string>());
            if (results[i].type == "text") {
                CHECK(results[i].value == expected[i]["value"].get<std::string>());
            } else {
                CHECK(results[i].value == expected[i]["value_hex"].get<std::string>());
            }
        }
    }
}
