# embedded-bridge (Python)

Host-side Python implementation of
[embedded-bridge](https://github.com/m-mcgowan/embedded-bridge), a
communication stack for embedded systems. It speaks the same wire protocol
as the C++ library that runs on the device.

```bash
pip install embedded-bridge              # core: no dependencies
pip install embedded-bridge[serial]      # + SerialTransport (pyserial)
pip install embedded-bridge[websocket]   # + WebSocketTransport
```

What's included:

- **Message protocol** (`MessageReader` / `MessageWriter`): text commands and
  length-prefixed binary payloads on one link
- **Framing**: HDLC, SLIP, COBS, line framing, CRC-16
- **Transports**: serial, WebSocket
- **Receivers**: crash detection, timestamped event capture, sleep/wake
  monitoring, memory tracking, and a router to fan output out to them
- **TestSession**: run on-device tests that survive sleep/wake cycles

```python
from embedded_bridge.framing.message import MessageReader, MessageWriter
```

Documentation, design notes, and the full feature list are in the
[project README](https://github.com/m-mcgowan/embedded-bridge#readme).
