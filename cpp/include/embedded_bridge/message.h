#pragma once

/// @file message.h
/// MessageReader / MessageWriter — application-layer message protocol.
///
/// Wire protocol:
///
///     printable text...\n                       — text message
///     \x01\x01<varint length><payload bytes>    — binary message
///
/// SOH (0x01) signals "binary message follows". The next byte is a protocol
/// version (currently 1), then a varint-encoded length, then exactly that
/// many payload bytes.  Any other leading byte starts a text message that
/// ends at '\n'.
///
/// Reader: dispatches to a MessageHandler as data arrives.
/// Writer: formats messages onto a Writer output sink.

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include <embedded_bridge/writer.h>

namespace ebridge {

static constexpr uint8_t SOH = 0x01;
static constexpr uint8_t BINARY_PROTOCOL_V1 = 0x01;

// Maximum varint bytes (5 bytes = up to 4 GB, matching protobuf uint32)
static constexpr size_t VARINT_MAX_BYTES = 5;

// ---------------------------------------------------------------------------
// Varint helpers
// ---------------------------------------------------------------------------

/// Encode an unsigned integer as a protobuf-style varint (LEB128).
/// Returns the number of bytes written to \p out (at most 5).
/// \p out must have room for at least 5 bytes.
inline size_t encode_varint(uint32_t value, uint8_t* out) {
    size_t n = 0;
    while (value > 0x7F) {
        out[n++] = static_cast<uint8_t>((value & 0x7F) | 0x80);
        value >>= 7;
    }
    out[n++] = static_cast<uint8_t>(value & 0x7F);
    return n;
}

/// Decode a varint from a byte buffer.
/// Returns the decoded value via \p out_value and the number of bytes
/// consumed via \p out_consumed.  Returns false on truncated or overlong
/// varint.
inline bool decode_varint(const uint8_t* data, size_t len,
                          uint32_t& out_value, size_t& out_consumed) {
    uint32_t value = 0;
    unsigned shift = 0;
    for (size_t i = 0; i < len && i < VARINT_MAX_BYTES; i++) {
        value |= static_cast<uint32_t>(data[i] & 0x7F) << shift;
        shift += 7;
        if ((data[i] & 0x80) == 0) {
            out_value = value;
            out_consumed = i + 1;
            return true;
        }
    }
    return false;
}

// ---------------------------------------------------------------------------
// MessageHandler — callback interface
// ---------------------------------------------------------------------------

/// Override to handle parsed messages.
///
/// For streaming (chunk-level) use, override on_text_data / on_text_end
/// and on_binary_start / on_binary_data / on_binary_end.
///
/// For complete messages, override on_text and on_binary (default
/// implementations buffer chunks and call these).
class MessageHandler {
public:
    virtual ~MessageHandler() = default;

    // -- Complete message callbacks (override these for simple use) --

    /// Called with a complete text line (no trailing newline).
    /// \p data is NOT null-terminated.
    virtual void on_text(const char* data, size_t len) { (void)data; (void)len; }

    /// Called with a complete binary payload.
    virtual void on_binary(const uint8_t* data, size_t len) { (void)data; (void)len; }

    // -- Streaming callbacks (override for zero-copy / low-memory) --

    /// Text bytes, delivered as they arrive.
    virtual void on_text_data(const char* data, size_t len) {
        (void)data; (void)len;
    }

    /// End of text message ('\n' received).
    virtual void on_text_end() {}

    /// Binary message header received; \p length payload bytes will follow.
    virtual void on_binary_start(uint32_t length) { (void)length; }

    /// A chunk of binary payload data.
    virtual void on_binary_data(const uint8_t* data, size_t len) {
        (void)data; (void)len;
    }

    /// All payload bytes for the current binary message have been delivered.
    virtual void on_binary_end() {}
};

// ---------------------------------------------------------------------------
// BufferingMessageHandler — collects chunks, delivers complete messages
// ---------------------------------------------------------------------------

/// Buffers streaming chunks and delivers complete messages via on_text()
/// and on_binary().  Override those two methods.
///
/// \tparam TextBufSize  max text line length (bytes)
/// \tparam BinaryBufSize  max binary payload length (bytes)
template <size_t TextBufSize = 512, size_t BinaryBufSize = 4096>
class BufferingMessageHandler : public MessageHandler {
public:
    void on_text_data(const char* data, size_t len) override {
        size_t take = (len < TextBufSize - _text_len) ? len : (TextBufSize - _text_len);
        memcpy(_text_buf + _text_len, data, take);
        _text_len += take;
    }

    void on_text_end() override {
        // Strip trailing \r (CRLF normalization)
        if (_text_len > 0 && _text_buf[_text_len - 1] == '\r') {
            _text_len--;
        }
        on_text(_text_buf, _text_len);
        _text_len = 0;
    }

    void on_binary_start(uint32_t length) override {
        _binary_len = 0;
        (void)length;
    }

    void on_binary_data(const uint8_t* data, size_t len) override {
        size_t take = (len < BinaryBufSize - _binary_len) ? len : (BinaryBufSize - _binary_len);
        memcpy(_binary_buf + _binary_len, data, take);
        _binary_len += take;
    }

    void on_binary_end() override {
        on_binary(_binary_buf, _binary_len);
        _binary_len = 0;
    }

private:
    char _text_buf[TextBufSize];
    size_t _text_len = 0;
    uint8_t _binary_buf[BinaryBufSize];
    size_t _binary_len = 0;
};

// ---------------------------------------------------------------------------
// MessageReader
// ---------------------------------------------------------------------------

/// Parses a byte stream into text and binary messages.
///
/// Feed raw bytes via feed(). The reader dispatches to a MessageHandler
/// as data arrives.
class MessageReader {
public:
    explicit MessageReader(MessageHandler& handler) : _handler(handler) {}

    /// Feed raw bytes from the transport.
    void feed(const uint8_t* data, size_t len) {
        size_t i = 0;
        while (i < len) {
            uint8_t c = data[i];

            switch (_state) {
                case State::IDLE:
                    if (c == SOH) {
                        _state = State::VERSION;
                        i++;
                    } else if (c == '\n') {
                        _handler.on_text_end();
                        i++;
                    } else {
                        _state = State::TEXT;
                        i = _feed_text(data, len, i);
                    }
                    break;

                case State::TEXT:
                    i = _feed_text(data, len, i);
                    break;

                case State::VERSION:
                    if (c == BINARY_PROTOCOL_V1) {
                        _state = State::LENGTH;
                        _varint_value = 0;
                        _varint_shift = 0;
                        _varint_bytes_read = 0;
                    } else {
                        // Unknown version — discard and return to idle
                        _state = State::IDLE;
                    }
                    i++;
                    break;

                case State::LENGTH:
                    _process_varint_byte(c);
                    i++;
                    break;

                case State::PAYLOAD:
                    i = _feed_payload(data, len, i);
                    break;
            }
        }
    }

    /// Reset the reader to its initial state.
    void reset() {
        _state = State::IDLE;
        _varint_value = 0;
        _varint_shift = 0;
        _varint_bytes_read = 0;
        _payload_remaining = 0;
    }

private:
    enum class State : uint8_t { IDLE, TEXT, VERSION, LENGTH, PAYLOAD };

    size_t _feed_text(const uint8_t* data, size_t len, size_t start) {
        size_t i = start;
        while (i < len) {
            uint8_t c = data[i];
            if (c == '\n') {
                if (i > start) {
                    _handler.on_text_data(
                        reinterpret_cast<const char*>(data + start), i - start);
                }
                _handler.on_text_end();
                _state = State::IDLE;
                return i + 1;
            } else if (c == SOH) {
                if (i > start) {
                    _handler.on_text_data(
                        reinterpret_cast<const char*>(data + start), i - start);
                }
                _handler.on_text_end();
                _state = State::VERSION;
                return i + 1;
            }
            i++;
        }
        // No delimiter — deliver what we have as a chunk
        if (i > start) {
            _handler.on_text_data(
                reinterpret_cast<const char*>(data + start), i - start);
        }
        return i;
    }

    size_t _feed_payload(const uint8_t* data, size_t len, size_t start) {
        size_t available = len - start;
        size_t take = (available < _payload_remaining) ? available : _payload_remaining;
        if (take > 0) {
            _handler.on_binary_data(data + start, take);
            _payload_remaining -= take;
        }
        if (_payload_remaining == 0) {
            _handler.on_binary_end();
            _state = State::IDLE;
        }
        return start + take;
    }

    void _process_varint_byte(uint8_t c) {
        _varint_value |= static_cast<uint32_t>(c & 0x7F) << _varint_shift;
        _varint_shift += 7;
        _varint_bytes_read++;

        if ((c & 0x80) == 0) {
            uint32_t length = _varint_value;
            _handler.on_binary_start(length);
            if (length == 0) {
                _handler.on_binary_end();
                _state = State::IDLE;
            } else {
                _payload_remaining = length;
                _state = State::PAYLOAD;
            }
        } else if (_varint_bytes_read >= VARINT_MAX_BYTES) {
            _state = State::IDLE;
        }
    }

    MessageHandler& _handler;
    State _state = State::IDLE;
    uint32_t _varint_value = 0;
    unsigned _varint_shift = 0;
    unsigned _varint_bytes_read = 0;
    uint32_t _payload_remaining = 0;
};

// ---------------------------------------------------------------------------
// MessageWriter
// ---------------------------------------------------------------------------

/// Formats text and binary messages onto a Writer output sink.
///
/// Convenience API:
///     writer.write_text("hello", 5);
///     writer.write_binary(payload, len);
///
/// Streaming API:
///     writer.begin_text();
///     writer.write(data, len);
///     writer.end();
///
///     writer.begin_binary(total_len);
///     writer.write(chunk1, len1);
///     writer.write(chunk2, len2);
///     writer.end();
class MessageWriter {
public:
    explicit MessageWriter(Writer& output) : _output(output) {}

    // -- Convenience API --

    /// Write a complete text message (adds trailing newline).
    void write_text(const char* text, size_t len) {
        _output.write(reinterpret_cast<const uint8_t*>(text), len);
        _output.write(static_cast<uint8_t>('\n'));
    }

    /// Write a complete text message (null-terminated).
    void write_text(const char* text) {
        write_text(text, strlen(text));
    }

    /// Write a complete binary message (SOH + version + varint length + payload).
    void write_binary(const uint8_t* payload, size_t len) {
        _write_binary_header(len);
        _output.write(payload, len);
    }

    // -- Streaming API --

    /// Start a streaming text message.
    void begin_text() {}

    /// Start a streaming binary message of \p length payload bytes.
    void begin_binary(uint32_t length) {
        _write_binary_header(length);
    }

    /// Write data within the current streaming message.
    void write(const uint8_t* data, size_t len) {
        _output.write(data, len);
    }

    /// Write text data within the current streaming text message.
    void write(const char* data, size_t len) {
        _output.write(reinterpret_cast<const uint8_t*>(data), len);
    }

    /// Finish a streaming text message (adds trailing newline).
    void end_text() {
        _output.write(static_cast<uint8_t>('\n'));
    }

    /// Finish a streaming binary message (no-op — length was declared upfront).
    void end_binary() {}

private:
    void _write_binary_header(uint32_t length) {
        _output.write(SOH);
        _output.write(BINARY_PROTOCOL_V1);
        uint8_t varint_buf[VARINT_MAX_BYTES];
        size_t varint_len = encode_varint(length, varint_buf);
        _output.write(varint_buf, varint_len);
    }

    Writer& _output;
};

}  // namespace ebridge
