#!/usr/bin/env python3
"""Mock engine for the Dictation app. Python 3 stdlib only.

Speaks the protocol in ../../PROTOCOL.md on 127.0.0.1:47321.
Partials echo the audio received so far, the final is a fixed sentence,
and word requests fix a few typos ("teh" -> "the").
Run: python3 app/tools/mock_engine.py
"""
import base64
import json
import socketserver
import time

HOST, PORT = "127.0.0.1", 47321
FIXES = {"teh": "the", "awya": "away", "adn": "and", "taht": "that", "recieve": "receive"}
FINAL_TEXT = "This is a test dictation from the mock engine."


T0 = time.time()


class Handler(socketserver.StreamRequestHandler):
    def send(self, obj):
        self.wfile.write((json.dumps(obj) + "\n").encode())
        self.wfile.flush()

    def handle(self):
        print("client connected", flush=True)
        nbytes = 0
        chunks = 0
        t_stop = 0.0
        mode = "clean"
        for line in self.rfile:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            kind = msg.get("type")
            if kind == "ping":
                self.send({"type": "pong", "models": {"asr": "mock", "tagger": "mock"}})
            elif kind == "status":
                self.send({"type": "status", "uptime_s": int(time.time() - T0), "asr_bits": 8,
                           "models": {"asr": True, "tagger": True, "context": True, "editor": False},
                           "counts": {"dictations": 3, "fixes": 7, "meetings": 1},
                           "errors": [{"t": "2026-11-02T10:00:00", "where": "context", "msg": "mock error"}]})
            elif kind == "start":
                nbytes = chunks = 0
                mode = msg.get("mode", "clean")
                print("start", mode, flush=True)
            elif kind == "audio":
                nbytes += len(base64.b64decode(msg.get("pcm16", "")))
                chunks += 1
                if chunks % 3 == 0:
                    secs = nbytes / 32000.0
                    self.send({"type": "partial", "text": "listening... %.1fs of audio" % secs})
            elif kind == "stop":
                t_stop = time.time()
                print("stop after %d chunks (%d bytes)" % (chunks, nbytes), flush=True)
                time.sleep(0.2)
                text = FINAL_TEXT if mode == "clean" else FINAL_TEXT.lower().rstrip(".")
                self.send({"type": "final", "text": text, "raw": text.lower(),
                           "ms": int((time.time() - t_stop) * 1000)})
            elif kind == "cancel":
                print("cancel", flush=True)
                nbytes = chunks = 0
            elif kind == "word":
                w = msg.get("word", "")
                fix = FIXES.get(w.lower())
                if fix and w[:1].isupper():
                    fix = fix.capitalize()
                self.send({"type": "word_result", "word": w, "fix": fix})
            elif kind == "feedback":
                print("feedback", msg, flush=True)
        print("client disconnected", flush=True)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    with Server((HOST, PORT), Handler) as srv:
        print("mock engine on %s:%d" % (HOST, PORT), flush=True)
        srv.serve_forever()
