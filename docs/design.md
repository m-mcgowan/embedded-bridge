# embedded-bridge — Design

Communication stack for embedded systems. Provides pluggable transport,
message framing, crash detection, sleep/wake monitoring, and test
orchestration — the host-side counterpart to embedded firmware.

## Motivation

Interacting with embedded firmware from a host computer is a recurring
need across testing, profiling, provisioning, and debugging. The same
patterns keep getting reimplemented:

- ppk2-python's `EventMapper` receives timestamped events from serial
- PlatformIO's test runner disconnects/reconnects serial during upload
- Ad-hoc scripts send commands and scrape serial output
- Crash detection scripts pattern-match for backtraces and watchdog resets
- `serial-monitor` provides interactive terminal access

These all share the same structure: signals flow between a device and a
host. The differences are in *what* the signals mean and *how* they're
carried — but the plumbing is the same.

embedded-bridge separates the **transport** (how signals are carried)
from the **semantics** (what you send and receive) so both are
independently pluggable. Serial is the most common transport, but WiFi,
BLE, and file replay use the same receivers.

## Architecture

The bridge handles **bidirectional signals** between host and device.
Each language implementation is a peer — it speaks the same wire protocol
and can sit on either side of the link.

```
┌─────────────────────────┐         ┌─────────────────────────┐
│       Side A            │  serial │       Side B            │
│                         │  USB    │                         │
│  Application            │  WiFi   │  Application            │
│    ↕                    │         │    ↕                    │
│  MessageReader/Writer   │ ←─────→ │  MessageReader/Writer   │
│    ↕                    │         │    ↕                    │
│  Framing (HDLC/SLIP/…)  │         │  Framing (HDLC/SLIP/…)  │
│    ↕                    │         │    ↕                    │
│  Transport              │         │  Transport              │
└─────────────────────────┘         └─────────────────────────┘
```

The typical case is **C++ on the device** (embedded firmware) and **Python
on the host** (tools, test runners, dashboards), but every implementation
is platform-neutral — the C++ library compiles on desktop and embedded
targets alike, and the Python library has no OS-specific dependencies.

### Stream processing pipeline

Everything starts as bytes. The incoming pipeline is:

1. **Transport** delivers raw bytes from the device
2. **Framing** segments bytes into messages — HDLC, SLIP, or COBS
   provide byte-level integrity on unreliable transports. On reliable
   transports (USB CDC, TCP), skip framing and use the message protocol
   directly.
3. **Message protocol** separates text and binary on a single stream —
   text ends at `\n`, binary starts with SOH + version + length
4. **Receivers** consume messages at whatever level they need

Receivers hook in at the appropriate pipeline stage:

- `CrashDetector` → lines (pattern matching on text)
- `EventCapture` → lines (Chrome JSON event markers)
- `SleepWakeMonitor` → lines (sleep/wake patterns) + port monitoring
- `MemoryTracker` → lines (PTR:MEM markers)
- `Router` → dispatches to multiple receivers with optional filters

This means receivers don't know about transport *or* framing. A
`CrashDetector` works the same whether its lines came from serial,
WiFi, or a log file replay.

### Using receivers without a transport

Receivers can be used independently — anything that can produce
messages can feed them:

- **PlatformIO test runners** feed lines from PIO's
  `on_testing_line_output()` callback — no transport or framing needed
- **Replay/analysis tools** feed lines from a log file
- **Custom integrations** feed from whatever source they have

---

## Wire Protocol

### Message protocol

Every message is self-identifying from its first byte:

**Text message:**
```
printable text...\n
```

**Binary message:**
```
SOH (0x01) | version (0x01) | varint length | payload bytes
```

The length prefix makes unknown messages skippable — a receiver that
doesn't understand the payload reads and discards `length` bytes to
reach the next boundary.

Both C++ and Python implement matched MessageReader/Writer peers that
interoperate on the wire. The Python implementation supports three
consumption tiers:

1. `drain()` — polling for complete messages
2. `MessageHandler` — subclass with `on_text()` / `on_binary()` callbacks
3. `StreamingMessageHandler` — zero-copy chunk processing for large
   binary transfers

### Framing

Framers sit below the message protocol and provide byte-level integrity
on unreliable transports. Three framers are implemented in both C++ and
Python with shared test vectors:

| Framer | Integrity | Overhead | Use case |
|--------|-----------|----------|----------|
| **HDLC** | CRC-16 | Flag bytes + byte stuffing | Noisy UART, needs error detection |
| **SLIP** | None | Escape bytes | Simple framing, no CRC needed |
| **COBS** | None | 1 byte per 254 | Deterministic overhead, no escaping |

Additionally, **LineFramer** (Python only) provides simple newline-based
framing for text-only streams.

On reliable transports (USB CDC, TCP), skip framing entirely — the
message protocol works directly on the byte stream.

---

## Transport Layer

### Interface

```python
class Transport(Protocol):
    """Bidirectional byte stream to/from an embedded device."""

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def read(self, size: int = -1, timeout: float | None = None) -> bytes: ...
    def write(self, data: bytes) -> None: ...
    def is_connected(self) -> bool: ...

    @property
    def port_path(self) -> str | None:
        """Underlying port path, if applicable (for sleep/wake detection)."""
        ...
```

Transports deliver and accept raw bytes — they know nothing about
lines, frames, or message boundaries.

### SerialTransport

The primary transport. Wraps pyserial with:

- Connect by device name (via usb-device registry) or port path
- Baud rate configuration
- Non-blocking read with timeout
- Reconnect on disconnect (configurable policy)
- Exclusive access management
- Port existence checking (for detecting USB-CDC disappearance during
  deep sleep)

---

## Receivers

Receivers consume incoming messages from the device. Each receiver is
independent and can be used alone or composed with others via `Router`.

### Interface

```python
class Receiver(Protocol):
    """Consumes incoming messages from the device."""

    def feed(self, message: bytes | str) -> None: ...
```

### CrashDetector

Monitors device output for crash indicators and hangs.

**Crash patterns detected:**
- `Backtrace:` / `backtrace:` — ESP32 crash dump
- `Guru Meditation Error` — ESP-IDF fatal error
- `panic_abort` / `abort()` — libc abort
- `Task watchdog got triggered` / `WDT reset` — watchdog timeout

**Hang detection:**
- Silent hang — no output for configurable duration (default 45s)

Crash patterns are configurable per platform — ESP32 patterns ship
built-in, but users can add patterns for other MCUs.

### EventCapture

Receives Chrome JSON trace events (lines matching `{"ph":...}`) and
pairs START/STOP markers into `EventSpan` objects with device and host
timestamps. Bridges between embedded-tracer serial output and ppk2-python's
event attribution.

### SleepWakeMonitor

Detects device sleep/wake transitions.

**Detection methods:**

1. **Serial pattern** — firmware prints sleep intent before sleeping
2. **Port disappearance** — `os.path.exists(port_path)` returns False
   when USB-CDC powers down during deep sleep
3. **Wake detection** — port reappears after disappearance

Sleep/wake patterns are configurable — the defaults match ESP32's
USB-CDC behavior but can be overridden for other platforms.

### MemoryTracker

Parses `PTR:MEM:BEFORE` and `PTR:MEM:AFTER` markers from device output
to track per-test heap usage. Reports memory deltas and detects leaks.

### Router

Routes messages to multiple receivers based on configurable filter
functions.

```python
router = Router([
    (event_capture,  lambda msg: msg.startswith("T=")),
    (crash_detector, None),  # None = receives all messages
])
```

Messages can match multiple receivers. Unmatched messages are available
via a passthrough handler.

---

## Testing Module

The `testing` subpackage provides test orchestration primitives for
driving device test sessions from the host.

### Types

- **`TestInfo`** — frozen dataclass with `id`, `name`, and `group` for
  test catalog entries
- **`TestOutcome`** — mutable dataclass capturing test execution results
  including status, markers, serial logs, warnings, and sleep measurements

### Protocol

Control characters (SOH, STX, ETX) and marker parsing for structured
test output:

- `parse_marker()` — extracts timestamp and payload from `T=...` lines
- `parse_json_line()` — extracts JSON metadata from serial output

### TestSession

Orchestrates test execution over a transport:

1. **Discovery** (SOH) — enumerate available tests
2. **Execution** (STX) — run tests individually or in batch
3. **Sleep monitoring** — detect USB-CDC disappearance during deep sleep,
   wait for wake, reconnect
4. **Result collection** — gather outcomes, markers, and serial logs

Sleep detection is injectable — the caller provides a callback for
port monitoring.

---

## C++ Implementation

Header-only, C++17. No dependencies beyond the standard library (Arduino
`Print` adapter auto-detected via `__has_include`).

```
include/embedded_bridge/
    message.h                    — MessageReader/Writer, varint helpers
    writer.h                     — Writer base class and subclasses
    detail/
        crc16.h                  — CRC-16/HDLC
    framing/
        hdlc.h                   — HDLC framer + writer (CRC-16)
        slip.h                   — SLIP framer + writer
        cobs.h                   — COBS framer + writer
```

---

## Integration

### With embedded-tracer

embedded-tracer provides device-side trace instrumentation (C++).
embedded-bridge's EventCapture receiver collects trace events from
embedded-tracer's serial output on the host side.

### With ppk2-python

ppk2-python uses embedded-bridge for event capture and trace
collection. The `ppk2 merge` command accepts trace data alongside
`.ppk2` power capture files.

### With pio-test-runner

pio-test-runner uses embedded-bridge's receivers (CrashDetector,
MemoryTracker, SleepWakeMonitor, Router) without its transport layer —
PlatformIO provides the message source.

### Dependency flow

```
embedded-tracer ──serial──→ embedded-bridge ──receivers──→ pio-test-runner
    (firmware, C++)            (host, Python)                (PIO plugin)
                                    │
                                    ├──→ ppk2-python (power correlation)
                                    └──→ Perfetto UI / reports
```

---

## Future

The following features are planned but not yet implemented:

- **Bridge class** — convenience wiring of transport + framing +
  receivers for standalone use
- **FileTransport** — replay captured log files as if from a live device
- **CommandEmitter** — structured host → device commands with ack handling
- **HybridFramer** — mixed text/binary framing on a single stream
  (auto-detect binary headers inline)
- **LengthPrefixedFramer** — binary-only framing with length headers

---

## Project Structure

```
embedded-bridge/
├── cpp/                             # C++ implementation (header-only, C++17)
│   ├── include/embedded_bridge/     # Public headers
│   │   ├── message.h
│   │   ├── writer.h
│   │   ├── detail/crc16.h
│   │   └── framing/
│   │       ├── hdlc.h
│   │       ├── slip.h
│   │       └── cobs.h
│   ├── test/                        # doctest-based tests
│   └── CMakeLists.txt
│
├── python/                          # Python implementation (3.10+)
│   ├── src/embedded_bridge/
│   │   ├── framing/                 # Message protocol + framers
│   │   ├── transport/               # SerialTransport
│   │   ├── receivers/               # CrashDetector, EventCapture, etc.
│   │   └── testing/                 # TestSession orchestration
│   ├── tests/                       # pytest-based tests
│   └── pyproject.toml
│
├── wire-tests/                      # Shared test vectors (JSON)
│   ├── message.json
│   ├── hdlc.json, slip.json, cobs.json
│   └── crc16.json
│
├── docs/design.md                   # This file
├── CHANGELOG.md
├── LICENSE                          # BSD-3-Clause
└── README.md
```
