#pragma once

#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

namespace ebridge {

/// Abstract output sink. Replaces Arduino Print& in all core code.
class Writer {
public:
    virtual ~Writer() = default;
    virtual size_t write(uint8_t c) = 0;

    virtual size_t write(const uint8_t* buf, size_t len) {
        size_t n = 0;
        for (size_t i = 0; i < len; i++) n += write(buf[i]);
        return n;
    }

    size_t print(const char* s) {
        if (!s) return 0;
        return write(reinterpret_cast<const uint8_t*>(s), strlen(s));
    }

    size_t print(char c) { return write(static_cast<uint8_t>(c)); }

    size_t print(int v) {
        char buf[16];
        snprintf(buf, sizeof(buf), "%d", v);
        return print(buf);
    }

    size_t print(unsigned int v) {
        char buf[16];
        snprintf(buf, sizeof(buf), "%u", v);
        return print(buf);
    }

    size_t print(long v) {
        char buf[24];
        snprintf(buf, sizeof(buf), "%ld", v);
        return print(buf);
    }

    size_t print(float v, int digits = 2) {
        char buf[24];
        snprintf(buf, sizeof(buf), "%.*f", digits, static_cast<double>(v));
        return print(buf);
    }

    size_t print(bool v) { return print(v ? "true" : "false"); }

    size_t println(const char* s = "") { return print(s) + print("\r\n"); }
    size_t println(int v) { return print(v) + print("\r\n"); }
    size_t println(float v, int digits = 2) { return print(v, digits) + print("\r\n"); }

    /// Flush a complete frame. No-op for plain writers; framing writers
    /// override to encode + emit the buffered payload as a framed message.
    virtual void end_frame() {}

    size_t printf(const char* fmt, ...) __attribute__((format(printf, 2, 3))) {
        char buf[128];
        va_list args;
        va_start(args, fmt);
        int n = vsnprintf(buf, sizeof(buf), fmt, args);
        va_end(args);
        if (n > 0) return print(buf);
        return 0;
    }
};

/// Collects output into a fixed-size char buffer. For tests and capture.
template <size_t N = 512>
class BufferWriter : public Writer {
public:
    BufferWriter() { clear(); }

    size_t write(uint8_t c) override {
        if (_pos < N) {
            _buf[_pos++] = static_cast<char>(c);
            _buf[_pos] = '\0';
            return 1;
        }
        _overflow = true;
        return 0;
    }

    void clear() {
        _pos = 0;
        _buf[0] = '\0';
        _overflow = false;
    }

    const char* str() const { return _buf; }
    size_t len() const { return _pos; }
    bool overflowed() const { return _overflow; }

private:
    char _buf[N + 1];
    size_t _pos;
    bool _overflow;
};

/// Writes to a FILE* (stdout by default).
class StdioWriter : public Writer {
public:
    explicit StdioWriter(FILE* f = stdout) : _f(f) {}
    size_t write(uint8_t c) override {
        fputc(c, _f);
        return 1;
    }

private:
    FILE* _f;
};

/// Discards all output.
class NullWriter : public Writer {
public:
    size_t write(uint8_t) override { return 1; }
    size_t write(const uint8_t*, size_t len) override { return len; }
};

/// Arduino Print& adapter. Only available when Arduino.h is present.
#if __has_include(<Arduino.h>)
#include <Arduino.h>
class PrintWriter : public Writer {
public:
    explicit PrintWriter(Print& p) : _p(p) {}
    size_t write(uint8_t c) override { return _p.write(c); }
    size_t write(const uint8_t* buf, size_t len) override { return _p.write(buf, len); }

private:
    Print& _p;
};
#endif

}  // namespace ebridge
