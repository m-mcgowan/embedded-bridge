# Changelog

All notable changes to this project will be documented in this file.
Follows [Keep a Changelog](https://keepachangelog.com/) conventions.

## [Unreleased]

### Added
- C++ message protocol — MessageReader/Writer with varint length encoding
- Python message protocol — matched peer implementation
- HDLC, SLIP, and COBS framers in both C++ and Python with CRC-16 integrity
- Serial transport (Python, pyserial-based)
- CrashDetector — ESP32 Guru Meditation, backtrace, watchdog, and silent hang detection
- EventCapture — timestamped T= markers for power profiling alignment
- SleepWakeMonitor — USB-CDC port disappearance and serial pattern detection
- MemoryTracker — per-test heap tracking
- Router — message routing to multiple receivers
- TestSession — test orchestration with discovery, sleep detection, and reconnection
- Design document covering architecture and stream processing pipeline

### Internal
- Restructured into `cpp/` and `python/` top-level directories
