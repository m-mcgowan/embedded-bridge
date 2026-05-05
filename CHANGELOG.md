# Changelog

All notable changes to this project will be documented in this file.
Follows [Keep a Changelog](https://keepachangelog.com/) conventions.

## [Unreleased]

## [0.2.0] — 2026-05-05

### Changed
- **BREAKING: Protocol prefix renamed `PTR:` → `ETST:`** (Embedded Test).
  The inline protocol parser used by `MemoryTracker` (and coordinated with
  `pio-test-runner`) now defaults to `ETST:`. Firmware emitting `PTR:`
  markers will no longer be recognised by default. Call
  `MemoryTracker.set_prefix("PTR:")` for backward compatibility, or
  update the device side to emit `ETST:`.
- `TestSession.monitor` now sends an ACK byte (`0x06`) to the device after
  recording a `SLEEP:` marker, before disconnecting the transport. This
  gives firmware a synchronisation point so measurement setup (e.g. PPK
  power profiling) can complete before the device drops USB-CDC.
  Firmware can report ACK receipt via a `SLEEP_ACK:<0|1>` `T=` marker.

### Added
- **WebSocketTransport** — synchronous WebSocket transport using
  `websockets.sync.client`. Receives text and binary frames, buffers them
  as raw bytes for the `Transport` byte-stream interface, and supports
  reconnect on connection loss. Optional dependency:
  `pip install embedded-bridge[websocket]`.
- `EventCapture` detects `uint32_t` timestamp wraps from
  `embedded-trace`'s `SerialTracer` (raw `ts` wraps every ~71.58 minutes).
  When a wrap is detected, subsequent events get `wrap_count * 2**32` µs
  added to their adjusted timestamps so Perfetto and downstream consumers
  see a monotonic stream across wraps.

### Fixed
- `EventCapture` wrap detection uses a `2**31` µs threshold instead of
  strict `<` comparison, avoiding false wraps caused by sub-millisecond
  timestamp jitter when events are emitted from multiple cores.
- `MemoryTracker` import paths updated for the `etst` package rename, and
  `set_prefix` correctly reconfigures the parser.

## [0.1.0] — 2026-03-17

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
