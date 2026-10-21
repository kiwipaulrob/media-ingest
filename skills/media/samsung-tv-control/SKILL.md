---
name: samsung-tv-control
description: "Control Samsung Tizen TVs via WebSocket."
version: 1.0.0
author: Hermes Agent
created: 21 June 2026
platforms: [linux]
metadata:
  hermes:
    tags: [samsung, tv, tizen, remote, wss, websocket, smartthings]
---

# Samsung TV Remote Control

Control Samsung Tizen TVs via the raw WebSocket remote protocol (bypasses SmartThings cloud).

## TV Details (Paul's Lounge)

| Detail | Value |
|--------|-------|
| **Model** | QA65S90FAEXNZ (Samsung 65" OLED S90C) |
| **IP** | 192.168.214.178 |
| **MAC** | b8:a0:b8:a1:66:f7 |
| **Connection** | Wired Ethernet |
| **OS** | Tizen |
| **Smart plug** | `tv_power` (real-time wattage, currently ~114W when on) |
| **SmartThings** | `media_player.samsung_7_series_65` (limited: off, volume, source, some playback) |
| **Auth Token** | `81945129` (saved in memory) |

## Protocol: WSS WebSocket on port 8002

The TV exposes a WebSocket remote control API on **port 8002 via TLS** (not plain WS). Plain WS connections to port 8002 get Connection Reset.

### Authentication

The TV uses token-based auth:
1. First connection with just `?name=<base64(name)>` returns a token in the `ms.channel.connect` event
2. Subsequent connections include `&token=<token>` in the URL
3. The token appears to be persistent (works across reconnections)

### WebSocket Frame Format

**CRITICAL**: This TV accepts **unmasked** WebSocket frames (normally forbidden by RFC 6455 for client frames, but works over TLS). Masked frames cause a close frame with "bad opcode" error.

```python
frame = bytearray()
frame.append(0x81)  # FIN + text opcode
length = len(payload)
if length < 126:
    frame.append(length)
elif length < 65536:
    frame.append(126)
    frame.extend(struct.pack('>H', length))
frame.extend(payload)  # UNMASKED
```

### Key Command Format

Send JSON via WebSocket text frame:

```json
{
  "method": "ms.remote.control",
  "params": {
    "Cmd": "Click",
    "DataOfCmd": "KEY_MUTE",
    "Option": "false",
    "TypeOfRemote": "SendRemoteKey"
  }
}
```

### Complete Python Script

```python
#!/usr/bin/env python3
import socket, base64, struct, time, os, ssl, json

TV_IP = "192.168.214.178"
PORT = 8002
TOKEN = "81945129"  # from pairing
NAME_B64 = base64.b64encode(b"Hermes Agent").decode()

def send_key(key_name):
    path = "/api/v2/channels/samsung.remote.control?name=" + NAME_B64 + "&token=" + TOKEN
    
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    sock = context.wrap_socket(sock, server_hostname=TV_IP)
    sock.connect((TV_IP, PORT))
    
    ws_key = base64.b64encode(os.urandom(16)).decode()
    request = (
        "GET " + path + " HTTP/1.1\r\n"
        "Host: " + TV_IP + ":" + str(PORT) + "\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Key: " + ws_key + "\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    sock.send(request.encode())
    time.sleep(0.5)
    
    data = b''
    try:
        chunk = sock.recv(4096)
        data += chunk
        if b'101' not in data:
            raise Exception("WebSocket upgrade failed")
    except:
        sock.close()
        return
    
    # Build and send unmasked frame
    cmd = json.dumps({
        "method": "ms.remote.control",
        "params": {
            "Cmd": "Click",
            "DataOfCmd": key_name,
            "Option": "false",
            "TypeOfRemote": "SendRemoteKey"
        }
    }).encode()
    
    frame = bytearray([0x81])
    l = len(cmd)
    if l < 126:
        frame.append(l)
    else:
        frame.append(126)
        frame.extend(struct.pack('>H', l))
    frame.extend(cmd)
    
    sock.send(bytes(frame))
    time.sleep(0.5)
    sock.close()
```

### Available Key Codes

| Key | Function |
|-----|----------|
| `KEY_POWER` | Toggle power |
| `KEY_MUTE` | Toggle mute |
| `KEY_VOLUP` / `KEY_VOLDOWN` | Volume control |
| `KEY_CHUP` / `KEY_CHDOWN` | Channel control |
| `KEY_HOME` | Smart Hub home screen |
| `KEY_MENU` | Settings menu |
| `KEY_SOURCE` | Source/input selector |
| `KEY_UP` / `KEY_DOWN` / `KEY_LEFT` / `KEY_RIGHT` | Navigation |
| `KEY_ENTER` | Select/OK |
| `KEY_RETURN` / `KEY_EXIT` | Back/Exit |
| `KEY_INFO` | Display info banner |
| `KEY_TOOLS` | Tools menu |
| `KEY_1`-`KEY_9`, `KEY_0` | Number pad |
| `KEY_NETFLIX` | Launch Netflix |
| `KEY_YOUTUBE` | Launch YouTube |
| `KEY_PRIME` | Launch Prime Video |
| `KEY_DISNEY` | Launch Disney+ |

## Differences from SmartThings Integration

| Capability | SmartThings (media_player) | Direct WSS Protocol |
|-----------|---------------------------|---------------------|
| Turn off | ✅ | ✅ |
| Turn on | ❌ (not exposed) | ✅ KEY_POWER |
| Volume | ✅ set/mute/step | ✅ full control |
| Source | ✅ TV/HDMI only | ✅ KEY_SOURCE cycle |
| Navigation | ❌ | ✅ arrow keys |
| Apps | ❌ | ✅ KEY_NETFLIX etc. |
| Text input | ❌ | ✅ via KEY_ codes |

## Re-pairing

If the token stops working (e.g. TV reset):
1. Connect WSS without token param
2. Read the `ms.channel.connect` event for a new token
3. Update the token in memory and skills

```python
# To get a new token, connect WITHOUT &token= in URL
path = "/api/v2/channels/samsung.remote.control?name=" + NAME_B64
# The TV's first WebSocket text frame will contain {"event":"ms.channel.connect","data":{"token":"XXXXX",...}}
```

## HTTP API (Port 8001)

The TV also has an HTTP API on port 8001:

```
GET http://192.168.214.178:8001/api/v2/
```
Returns device info: model, OS (Tizen), power state, MAC, IP, resolution, etc.
No control endpoints exposed on HTTP (all control via WSS on 8002).

## Key Commands — Current Status (WIP)

The WSS connection **succeeds** (gets WebSocket 101 upgrade, receives connect event with token), and **unmasked frames are accepted** (TV responds with WebSocket ping rather than closing). However, key commands sent so far (KEY_MUTE, KEY_VOLUP, KEY_INFO) have **no visible effect** on the TV. This needs debugging.

### What's confirmed working
- ✅ WSS connection to port 8002 (TLS)
- ✅ Token auth — `?name=<b64>&token=<token>` in URL
- ✅ Unmasked WebSocket frames — TV accepts them
- ✅ Correct JSON command format (TV doesn't close, just pings)

### Suspected issues (not yet resolved)
- ❓ TV may expect `Cmd` value other than `"Click"` (try `"SendRemoteKey"` as top-level key, or `"Press"`/`"Release"` two-step)
- ❓ TV may need `"Option": "true"` or different option format
- ❓ Frame may need to remain connected and handle pings/pongs before command is processed
- ❓ Different JSON structure — some Samsung models use a different key format
- ❓ TV firmware (Tizen 2026) may have changed the protocol

### Debugging tips for next session
```python
# Try different Cmd values
for cmd_val in ["Click", "Press", "SendRemoteKey", "KeyDown", "KeyUp"]:
    cmd = {"method": "ms.remote.control",
           "params": {"Cmd": cmd_val, "DataOfCmd": "KEY_MUTE",
                      "Option": "false", "TypeOfRemote": "SendRemoteKey"}}

# Try keeping connection alive and handling pings
# The TV sends opcode 9 (ping) after receiving a frame — respond with pong (opcode 10)

# Try different JSON structure (some older TVs expect flat keys)
cmd = {"method": "ms.remote.control",
       "Cmd": "Click", "DataOfCmd": "KEY_MUTE"}

# Try sending raw key byte codes instead of JSON
```

## Pitfalls

- Plain WS (`ws://`) on port 8002 gets **Connection Reset** — must use **WSS** (`wss://`) with TLS
- Client frames must be **unmasked** — masked frames cause close with "protocol error"
- Token is obtained on first connect and appears persistent
- TV must be ON (powered) to respond — check `PowerState` via HTTP API or smart plug wattage
