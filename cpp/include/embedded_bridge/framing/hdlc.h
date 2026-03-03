#pragma once

#include <stddef.h>
#include <stdint.h>

#include <embedded_bridge/detail/crc16.h>
#include <embedded_bridge/writer.h>

namespace ebridge {

/// Callback when a complete, validated HDLC frame payload is ready.
using FrameCallback = void (*)(void* ctx, const uint8_t* data, size_t len);

// HDLC framing constants
namespace hdlc {
static constexpr uint8_t FLAG = 0x7E;
static constexpr uint8_t ESC = 0x7D;
static constexpr uint8_t ESC_XOR = 0x20;
static constexpr uint8_t XON = 0x11;
static constexpr uint8_t XOFF = 0x13;
}  // namespace hdlc

/// HDLC-like frame decoder (RFC 1662 style).
///
/// Feed raw bytes via process_byte(). On valid frame (good CRC-16/HDLC),
/// invokes the callback with the un-stuffed, CRC-stripped payload.
/// Corrupt or overflowed frames are silently discarded.
template <size_t BufSize = 256>
class HdlcFramer {
public:
    HdlcFramer(FrameCallback cb, void* ctx) : _cb(cb), _ctx(ctx) {}

    void process_byte(uint8_t c) {
        // XON/XOFF filtering (when enabled)
        if (_flow_control && (c == hdlc::XON || c == hdlc::XOFF)) return;

        switch (_state) {
            case State::IDLE:
                if (c == hdlc::FLAG) {
                    _reset_buf();
                    _state = State::IN_FRAME;
                }
                break;

            case State::IN_FRAME:
                if (c == hdlc::FLAG) {
                    _deliver();
                    _reset_buf();
                    // stay IN_FRAME — next flag starts new frame
                } else if (c == hdlc::ESC) {
                    _state = State::ESCAPE;
                } else {
                    _store(c);
                }
                break;

            case State::ESCAPE:
                if (c == hdlc::FLAG) {
                    // Abort — re-sync
                    _reset_buf();
                    _state = State::IN_FRAME;
                } else {
                    _store(c ^ hdlc::ESC_XOR);
                    _state = State::IN_FRAME;
                }
                break;

            case State::ERROR:
                if (c == hdlc::FLAG) {
                    _reset_buf();
                    _state = State::IN_FRAME;
                }
                break;
        }
    }

    void reset() {
        _state = State::IDLE;
        _reset_buf();
    }

    void set_flow_control(bool enable) { _flow_control = enable; }

private:
    enum class State : uint8_t { IDLE, IN_FRAME, ESCAPE, ERROR };

    void _reset_buf() { _len = 0; }

    void _store(uint8_t c) {
        if (_len < BufSize) {
            _buf[_len++] = c;
        } else {
            _state = State::ERROR;
        }
    }

    void _deliver() {
        // Need at least 2 CRC bytes
        if (_len < 2) return;

        // Verify CRC: run over entire buffer (payload + CRC bytes)
        uint16_t crc = detail::CRC16_INIT;
        for (size_t i = 0; i < _len; i++) {
            crc = detail::crc16_hdlc_update(crc, _buf[i]);
        }
        // After final XOR, valid frame gives CRC16_GOOD
        if ((crc ^ 0xFFFF) != detail::CRC16_GOOD) return;

        // Deliver payload (strip 2 CRC bytes)
        _cb(_ctx, _buf, _len - 2);
    }

    FrameCallback _cb;
    void* _ctx;
    State _state = State::IDLE;
    bool _flow_control = false;
    uint8_t _buf[BufSize];
    size_t _len = 0;
};

/// HDLC framing writer — accumulates payload, encodes + flushes on end_frame().
///
/// Usage: write payload bytes normally, then call end_frame() to emit
/// a complete HDLC frame (FLAG + byte-stuffed payload + CRC + FLAG)
/// to the downstream Writer.
template <size_t BufSize = 256>
class HdlcFramingWriter : public Writer {
public:
    explicit HdlcFramingWriter(Writer& downstream) : _downstream(downstream) {}

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

        // Compute CRC over payload
        uint16_t crc = detail::CRC16_INIT;
        for (size_t i = 0; i < _len; i++) {
            crc = detail::crc16_hdlc_update(crc, _buf[i]);
        }
        crc ^= 0xFFFF;  // final complement

        // Opening flag
        _downstream.write(hdlc::FLAG);

        // Byte-stuffed payload
        for (size_t i = 0; i < _len; i++) {
            _emit_stuffed(_buf[i]);
        }

        // CRC bytes (little-endian), byte-stuffed
        _emit_stuffed(static_cast<uint8_t>(crc & 0xFF));
        _emit_stuffed(static_cast<uint8_t>(crc >> 8));

        // Closing flag
        _downstream.write(hdlc::FLAG);

        discard();
    }

    void discard() {
        _len = 0;
        _overflow = false;
    }

    size_t buffered() const { return _len; }
    bool overflowed() const { return _overflow; }

private:
    void _emit_stuffed(uint8_t c) {
        if (c == hdlc::FLAG || c == hdlc::ESC) {
            _downstream.write(hdlc::ESC);
            _downstream.write(static_cast<uint8_t>(c ^ hdlc::ESC_XOR));
        } else {
            _downstream.write(c);
        }
    }

    Writer& _downstream;
    uint8_t _buf[BufSize];
    size_t _len = 0;
    bool _overflow = false;
};

}  // namespace ebridge
