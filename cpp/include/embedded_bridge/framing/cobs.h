#pragma once

#include <stddef.h>
#include <stdint.h>

#include <embedded_bridge/writer.h>

namespace ebridge {

/// COBS (Consistent Overhead Byte Stuffing) frame decoder.
///
/// Delimiter is 0x00. Feed raw bytes via process_byte().
/// On complete frame (0x00 delimiter), decodes COBS and delivers payload.
/// No built-in CRC — add application-layer integrity checking if needed.
template <size_t BufSize = 256>
class CobsFramer {
public:
    CobsFramer(FrameCallback cb, void* ctx) : _cb(cb), _ctx(ctx) {}

    void process_byte(uint8_t c) {
        if (c == 0x00) {
            // Frame delimiter
            if (_raw_len > 0 && !_error) {
                // Decode COBS in-place
                size_t decoded_len = 0;
                if (_cobs_decode(_raw_buf, _raw_len, _buf, BufSize, decoded_len)) {
                    _cb(_ctx, _buf, decoded_len);
                }
            }
            _raw_len = 0;
            _error = false;
        } else {
            if (_raw_len < BufSize + 1) {  // COBS adds at most 1 byte overhead
                _raw_buf[_raw_len++] = c;
            } else {
                _error = true;
            }
        }
    }

    void reset() {
        _raw_len = 0;
        _error = false;
    }

private:
    static bool _cobs_decode(const uint8_t* src, size_t src_len,
                             uint8_t* dst, size_t dst_cap, size_t& out_len) {
        out_len = 0;
        size_t i = 0;
        while (i < src_len) {
            uint8_t code = src[i++];
            if (code == 0) return false;  // unexpected zero in encoded data
            uint8_t count = code - 1;
            if (i + count > src_len) return false;  // truncated
            for (uint8_t j = 0; j < count; j++) {
                if (out_len >= dst_cap) return false;  // overflow
                dst[out_len++] = src[i++];
            }
            // If code < 0xFF and there's more data, emit a zero separator
            if (code < 0xFF && i < src_len) {
                if (out_len >= dst_cap) return false;
                dst[out_len++] = 0x00;
            }
        }
        return true;
    }

    FrameCallback _cb;
    void* _ctx;
    uint8_t _raw_buf[BufSize + 1];  // +1 for COBS overhead
    uint8_t _buf[BufSize];
    size_t _raw_len = 0;
    bool _error = false;
};

/// COBS framing writer — accumulates payload, encodes + flushes on end_frame().
///
/// Emits: COBS-encoded payload + 0x00 delimiter.
template <size_t BufSize = 256>
class CobsFramingWriter : public Writer {
public:
    explicit CobsFramingWriter(Writer& downstream) : _downstream(downstream) {}

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

        // COBS encode payload into temp buffer, then emit + delimiter
        // Max encoded size: payload_len + ceil(payload_len/254) + 1
        // For BufSize=256, worst case ~258 bytes
        uint8_t encoded[BufSize + (BufSize / 254) + 2];
        size_t enc_len = _cobs_encode(_buf, _len, encoded);

        _downstream.write(encoded, enc_len);
        _downstream.write(static_cast<uint8_t>(0x00));  // delimiter

        discard();
    }

    void discard() {
        _len = 0;
        _overflow = false;
    }

    size_t buffered() const { return _len; }
    bool overflowed() const { return _overflow; }

private:
    static size_t _cobs_encode(const uint8_t* src, size_t src_len, uint8_t* dst) {
        size_t dst_pos = 0;
        size_t code_pos = dst_pos++;  // reserve space for first code byte
        uint8_t code = 1;

        for (size_t i = 0; i < src_len; i++) {
            if (src[i] == 0x00) {
                dst[code_pos] = code;
                code_pos = dst_pos++;
                code = 1;
            } else {
                dst[dst_pos++] = src[i];
                code++;
                if (code == 0xFF) {
                    dst[code_pos] = code;
                    code_pos = dst_pos++;
                    code = 1;
                }
            }
        }
        dst[code_pos] = code;
        return dst_pos;
    }

    Writer& _downstream;
    uint8_t _buf[BufSize];
    size_t _len = 0;
    bool _overflow = false;
};

}  // namespace ebridge
