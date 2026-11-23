"""End-to-end test: speak with `say`, stream to the running engine like the app does.

    .venv/bin/python -m engine.test_client
Needs `python -m engine.server` running. Writes build/e2e.txt.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import tempfile
import time

import numpy as np

from engine.asr import SR, wav_to_array

CASES = [
    ("Um, so I think we should, uh, meet at three on Tuesday, actually no, Wednesday.",
     "So I think we should meet at three on Wednesday."),
    ("I talked to him, her, her about the Acme2Go blog post and it's, like, fine.",
     "I talked to her about the Acme2Go blog post and it's fine."),
    ("Okay. Um, yeah. Actually, no. Let's finish the ABCD101 homework tonight.",
     "Let's finish the ABCD101 homework tonight."),
    ("We should meet at three on Tuesday. Actually make that Wednesday.",
     "We should meet at three on Wednesday."),
    ("I really like this plan, you know, it's, it's really good.",
     "I really like this plan, it's really good."),
]


def speak(text, tmp, i):
    aiff, wav = os.path.join(tmp, f"c{i}.aiff"), os.path.join(tmp, f"c{i}.wav")
    subprocess.run(["say", "-o", aiff, text], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", aiff, wav], check=True)
    return wav_to_array(wav)


def main():
    s = socket.create_connection(("127.0.0.1", 47321))
    f = s.makefile("r")

    def send(o):
        s.sendall((json.dumps(o) + "\n").encode())

    def recv_until(kind):
        while True:
            m = json.loads(f.readline())
            if m["type"] == kind:
                return m

    out = []
    send({"type": "ping"})
    out.append("ping: " + json.dumps(recv_until("pong")))
    with tempfile.TemporaryDirectory() as tmp:
        for i, (said, want) in enumerate(CASES):
            audio = speak(said, tmp, i)
            audio = np.concatenate([audio, np.zeros(SR // 5, dtype=np.float32)])
            pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
            send({"type": "start", "mode": "clean"})
            step = SR // 10 * 2  # 100 ms of int16
            for j in range(0, len(pcm), step):
                send({"type": "audio", "pcm16": base64.b64encode(pcm[j : j + step]).decode()})
                time.sleep(0.1)
            t = time.perf_counter()
            send({"type": "stop"})
            m = recv_until("final")
            wall = (time.perf_counter() - t) * 1000
            out += ["", f"said: {said}", f"raw:  {m['raw']}", f"got:  {m['text']}", f"want: {want}",
                    f"engine {m['ms']} ms, wall {wall:.0f} ms"]
    for w, before in [("awya", "I'm going"), ("teh", "this is"), ("anthing", "do you need"), ("Zorpify", "working on")]:
        send({"type": "word", "word": w, "before": before})
        t = time.perf_counter()
        m = recv_until("word_result")
        out.append(f"word {w!r} -> {m['fix']!r} ({(time.perf_counter()-t)*1000:.1f} ms)")
    send({"type": "ping"})
    out.append("ping: " + json.dumps(recv_until("pong")))
    os.makedirs("build", exist_ok=True)
    open("build/e2e.txt", "w").write("\n".join(out) + "\n")
    print("\n".join(out))
    print("E2E_DONE")


if __name__ == "__main__":
    main()
