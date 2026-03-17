# Changelog

All notable changes to this project will be documented in this file.
Follows [Keep a Changelog](https://keepachangelog.com/) conventions.

## [Unreleased]

First release. Core wire protocol, framing, and host-side receivers.

### Added
- **Message protocol** — matched C++ and Python implementations with
  varint-encoded binary messages and text lines on a single stream
- **Framing** — HDLC (CRC-16), SLIP, and COBS framers in both C++ and
  Python, with shared JSON test vectors for cross-language validation
- **CRC-16/HDLC** — matched C++ and Python implementations
- **SerialTransport** — pyserial-based transport with reconnection,
  exclusive access, and port disappearance detection
- **CrashDetector** — ESP32 Guru Meditation, backtrace, watchdog, and
  silent hang detection
- **EventCapture** — Chrome JSON trace event parsing with START/STOP
  span pairing and host/device timestamp correlation
- **SleepWakeMonitor** — USB-CDC port disappearance and serial pattern
  detection for deep sleep transitions
- **MemoryTracker** — per-test heap tracking via PTR:MEM markers
- **Router** — message routing to multiple receivers with optional filters
- **TestSession** — test orchestration with discovery, execution, sleep
  detection, and reconnection
- **LineFramer** — simple newline-based framing for text streams
- **C++ header-only library** — message protocol and framers, C++17, no
  dependencies (Arduino `Print` auto-detected)
- **PlatformIO library.json** — for C++ header discovery via `lib_deps`
- Design documentation and architecture overview
