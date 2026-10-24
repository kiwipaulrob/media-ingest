#!/usr/bin/env python3
"""Samsung TV remote - send a key command"""
import socket
import base64
import struct
import os
import ssl
import json
import sys
import time

TV_IP = "192.168.214.178"
PORT = 8002
TOKEN_STR = "81945129"
NAME_B64 = base64.b64encode(b"Hermes Agent").decode()

def send_key(key_name):
    sep = chr(38)
    path = "/api/v2/channels/samsung.remote.control?name=" + NAME_B64 + sep + "token=" + TOKEN_STR

    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    sock = context.wrap_socket(sock, server_hostname=TV_IP)
    sock.connect((TV_IP, PORT))

    ws_key = base64.b64encode(os.urandom(16)).decode()
    req_lines = [
        "GET " + path + " HTTP/1.1",
        "Host: " + TV_IP + ":8002",
        "Upgrade: websocket",
        "Connection: Upgrade",
        "Sec-WebSocket-Key: " + ws_key,
        "Sec-WebSocket-Version: 13",
        "",
        ""
    ]
    request = (chr(13) + chr(10)).join(req_lines)
    sock.send(request.encode())
    time.sleep(0.5)

    try:
        chunk = sock.recv(4096)
        if b"101" not in chunk:
            print("Upgrade failed")
            sock.close()
            return False
    except Exception:
        print("No response")
        sock.close()
        return False

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
    length = len(cmd)
    if length < 126:
        frame.append(length)
    else:
        frame.append(126)
        frame.extend(struct.pack(">H", length))
    frame.extend(cmd)

    sock.send(bytes(frame))
    time.sleep(0.5)
    sock.close()
    print("Sent: " + key_name)
    return True

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 samsung_remote.py KEY_MUTE")
        print("Common keys: KEY_POWER KEY_MUTE KEY_VOLUP KEY_VOLDOWN")
        print("            KEY_HOME KEY_SOURCE KEY_NETFLIX KEY_YOUTUBE")
        sys.exit(1)
    send_key(sys.argv[1])
