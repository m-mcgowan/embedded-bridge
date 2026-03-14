#pragma once

#include <stddef.h>
#include <stdint.h>

#include <embedded_bridge/writer.h>

namespace ebridge {

// SLIP framing constants (RFC 1055)
namespace slip {
static constexpr uint8_t END = 0xC0;
static constexpr uint8_t ESC = 0xDB;
static constexpr uint8_t ESC_END = 0xDC;
static constexpr uint8_t ESC_ESC = 0xDD;
}  // namespace slip

/// SLIP frame decoder (RFC 1055).
///
/// Feed raw bytes via process_byte(). On complete frame (END delimiter),
/// invokes the callback with the un-stuffed payload.
/// No built-in CRC — add application-layer integrity checking if needed.
template <size_t BufSize = 256>
class SlipFramer {
public:
    SlipFramer(FrameCallback cb, void* ctx) : _cb(cb), _ctx(ctx) {}

    void process_byte(uint8_t c) {
        switch (c) {
            case slip::END:
                if (_len > 0 && !_error) {
                    _cb(_ctx, _buf, _len);
                }
                _len = 0;
                _error = false;
                _escape = false;
                break;

            case slip::ESC:
                _escape = true;
                break;

            default:
                if (_escape) {
                    _escape = false;
                    if (c == slip::ESC_END) {
                        _store(slip::END);
                    } else if (c == slip::ESC_ESC) {
                        _store(slip::ESC);
                    } else {
                        // Protocol error — invalid escape sequence
                        _error = true;
                    }
                } else {
                    _store(c);
                }
                break;
        }
    }

    void reset() {
        _len = 0;
        _error = false;
        _escape = false;
    }

private:
    void _store(uint8_t c) {
        if (_len < BufSize) {
            _buf[_len++] = c;
        } else {
            _error = true;
        }
    }

    FrameCallback _cb;
    void* _ctx;
    uint8_t _buf[BufSize];
    size_t _len = 0;
    bool _error = false;
    bool _escape = false;
};

/// SLIP framing writer — accumulates payload, encodes + flushes on end_frame().
///
/// Emits: END + byte-stuffed payload + END (double-END framing for robustness).
template <size_t BufSize = 256>
class SlipFramingWriter : public Writer {
public:
    explicit SlipFramingWriter(Writer& downstream) : _downstream(downstream) {}

    size_t write(uint8_t c) override {
        if (_len < BufSize) {
            _buf[_len++] = c;
            return 1;
        }
        _overflow = true;
        return 0;
    }

    size_t write(const uint8_t* buf, size_t len) override {
        size_t n = 0;
        for (size_t i = 0; i < len; i++) n += write(buf[i]);
        return n;
    }

    void end_frame() override {
        if (_overflow) {
            discard();
            return;
        }

        // Leading END (flushes any line noise)
        _downstream.write(slip::END);

        // Byte-stuffed payload
        for (size_t i = 0; i < _len; i++) {
            uint8_t c = _buf[i];
            if (c == slip::END) {
                _downstream.write(slip::ESC);
                _downstream.write(slip::ESC_END);
            } else if (c == slip::ESC) {
                _downstream.write(slip::ESC);
                _downstream.write(slip::ESC_ESC);
            } else {
                _downstream.write(c);
            }
        }

        // Trailing END
        _downstream.write(slip::END);

        discard();
    }

    void discard() {
        _len = 0;
        _overflow = false;
    }

    size_t buffered() const { return _len; }
    bool overflowed() const { return _overflow; }

private:
    Writer& _downstream;
    uint8_t _buf[BufSize];
    size_t _len = 0;
    bool _overflow = false;
};

}  // namespace ebridge
