"""Sends a wav to the running engine as a mic-only meeting (engine running).

    .venv/bin/python -m engine.test_meeting_client clip.wav [Speaker]
"""

import base64
import json
import os
import socket
import sys

import numpy as np

from engine.asr import SR, wav_to_array

audio = wav_to_array(sys.argv[1])
speaker = sys.argv[2] if len(sys.argv) > 2 else "Test"
s = socket.create_connection(("127.0.0.1", int(os.environ.get("DICTATION_PORT", "47321"))))
f = s.makefile("rw")


def send(o):
    f.write(json.dumps(o) + "\n")
    f.flush()


send({"type": "meeting_start", "system": False})
send({"type": "meeting_speaker", "name": speaker})
step = SR // 2
for i in range(0, len(audio), step):
    pcm = (np.clip(audio[i:i + step], -1, 1) * 32767).astype("<i2").tobytes()
    send({"type": "meeting_audio", "source": "mic", "pcm16": base64.b64encode(pcm).decode()})
send({"type": "meeting_stop"})
for line in f:
    m = json.loads(line)
    if m["type"].startswith("meeting"):
        print(m)
    if m["type"] == "meeting_done":
        break
