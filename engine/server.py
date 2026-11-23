"""Dictation engine. Speaks PROTOCOL.md on 127.0.0.1:47321.

Run in a normal terminal (needs the GPU):
    .venv/bin/python -m engine.server
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import sys
import threading
import time
from collections import deque
from datetime import datetime

import numpy as np

from engine.asr import ASR, DictationSession
from engine.vocab import VocabFixer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOST, PORT = "127.0.0.1", int(os.environ.get("DICTATION_PORT", "47321"))
FEEDBACK = os.environ.get("DICTATION_FEEDBACK", os.path.join(ROOT, "data", "raw", "feedback.jsonl"))
TAGGER_DIR = os.path.join(ROOT, ".models", "tagger")


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


# Fallback cleanup if the trained tagger isn't there yet: plain fillers only.
_FILLER = re.compile(r"(?i)(^|[\s,])(um+|uh+|uhm|erm|er|ah)([\s,.!?]|$)")


BACKREF = re.compile(r"(?i)\b(make that|change that to|change the \w+ (one|item|thing) to|scratch that)\b")


def rule_clean(text: str) -> tuple[str, bool]:
    prev = None
    while prev != text:
        prev = text
        text = _FILLER.sub(lambda m: m.group(1) if m.group(3) in " ," else m.group(3), text)
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r",\s*([.!?])", r"\1", text)
    text = re.sub(r"^[,\s]+", "", text)
    text = re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)
    needs_edit = bool(BACKREF.search(text))
    return re.sub(r" {2,}", " ", text).strip(), needs_edit


class Engine:
    def __init__(self):
        self.started = time.time()
        self.errors: deque = deque(maxlen=50)  # last 50 caught failures, shown in the hub
        self.counts = {"dictations": 0, "fixes": 0, "meetings": 0}  # since engine start
        self._context_error_seen = False
        from engine import meeting

        meeting.error_hook = self.record_error
        t = time.perf_counter()
        self.asr = ASR()
        log(f"ASR ready ({self.asr.model_id}, {time.perf_counter()-t:.1f}s)")
        vocab_path = os.path.join(ROOT, "data", "vocab.txt")
        self.vocab = VocabFixer(vocab_path)
        self.tagger = None
        try:
            from engine.tagger import Tagger

            if os.path.isdir(TAGGER_DIR):
                self.tagger = Tagger(TAGGER_DIR)
                log("tagger ready")
        except Exception as e:  # noqa: BLE001
            log("tagger not loaded, using rule cleanup:", e)
            self.record_error("tagger", f"not loaded, using rule cleanup: {e}")
        self.autocorrect = None
        try:
            from engine.autocorrect import Autocorrect

            self.autocorrect = Autocorrect(
                os.path.join(ROOT, "data", "typo_map.json"), vocab_path
            )
            log("autocorrect ready")
        except Exception as e:  # noqa: BLE001
            log("autocorrect not loaded:", e)
            self.record_error("server", f"autocorrect not loaded: {e}")
        from engine.editor import Editor

        self.editor = Editor()
        if self.tagger is None and not os.path.isdir(TAGGER_DIR):
            self.record_error("tagger", "model folder missing, using rule cleanup")

    def record_error(self, where: str, msg: str):
        self.errors.append({"t": datetime.now().isoformat(timespec="seconds"), "where": where,
                            "msg": str(msg)[:500]})

    def status(self) -> dict:
        ac = self.autocorrect
        if ac is not None and getattr(ac, "context_error", None) and not self._context_error_seen:
            self._context_error_seen = True
            self.record_error("context", f"model failed to load: {ac.context_error}")
        return {"type": "status", "uptime_s": int(time.time() - self.started),
                "asr_bits": self.asr.bits or 0,
                "models": {"asr": self.asr is not None, "tagger": self.tagger is not None,
                           "context": ac is not None and getattr(ac, "context", None) is not None,
                           "editor": self.editor.loaded()},
                "counts": dict(self.counts), "errors": list(self.errors)}

    def clean(self, raw: str, mode: str) -> str:
        text = self.vocab.fix(raw)
        if mode == "raw":
            return text
        if self.tagger:
            text, needs_edit = self.tagger.clean(text)
        else:
            text, needs_edit = rule_clean(text)
        if needs_edit:
            # Edit only the sentences that hold the back-reference, plus the one before.
            sents = re.split(r"(?<=[.!?])\s+", text)
            for i, s in enumerate(sents):
                if re.search(r"(?i)\b(make that|change that|change the|i mean|scratch that)\b", s):
                    lo = max(0, i - 1)
                    fixed = self.editor.fix(" ".join(sents[lo : i + 1]))
                    sents[lo : i + 1] = [fixed] + [""] * (i - lo)
            text = " ".join(s for s in sents if s)
        return text


def write_feedback(obj: dict):
    os.makedirs(os.path.dirname(FEEDBACK), exist_ok=True)
    obj["ts"] = datetime.now().isoformat(timespec="seconds")
    with open(FEEDBACK, "a") as f:
        f.write(json.dumps(obj) + "\n")


async def handle(engine: Engine, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    loop = asyncio.get_running_loop()
    send_lock = asyncio.Lock()
    session: DictationSession | None = None
    mode = "clean"
    sid = None
    meeting = None  # MeetingSession, independent of the dictation session
    log("client connected")

    def finalize_meeting(m):
        from engine.meeting import finalize

        def progress(state):
            asyncio.run_coroutine_threadsafe(send({"type": "meeting_status", "state": state}), loop)

        res = finalize(m, progress)
        engine.counts["meetings"] += 1
        asyncio.run_coroutine_threadsafe(send({"type": "meeting_done", **res}), loop)

    async def send(obj):
        async with send_lock:
            writer.write((json.dumps(obj) + "\n").encode())
            await writer.drain()

    def partial_cb(text):
        if BACKREF.search(text) and not engine.editor.loaded():
            engine.editor.preload()  # load while the user is still talking
        asyncio.run_coroutine_threadsafe(send({"type": "partial", "text": engine.vocab.fix(text)}), loop)

    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = msg.get("type")
            if kind == "ping":
                await send({"type": "pong", "models": {
                    "asr": engine.asr.model_id,
                    "tagger": "onnx" if engine.tagger else "rules",
                    "editor_loaded": engine.editor.loaded(),
                }})
            elif kind == "status":
                await send(engine.status())
            elif kind == "meeting_list":
                from engine import meeting as mt

                try:
                    items = await loop.run_in_executor(None, mt.list_meetings)
                except Exception as e:  # noqa: BLE001
                    engine.record_error("meeting", f"meeting_list: {e}")
                    items = []
                await send({"type": "meeting_list", "meetings": items})
            elif kind == "meeting_get":
                from engine import meeting as mt

                try:
                    res = await loop.run_in_executor(None, mt.get_meeting, msg.get("stamp"))
                except Exception as e:  # noqa: BLE001
                    engine.record_error("meeting", f"meeting_get: {e}")
                    res = {"stamp": msg.get("stamp"), "segments": [], "summary": "", "error": str(e)}
                await send({"type": "meeting_get", **res})
            elif kind == "meeting_retag":
                from engine import meeting as mt

                try:
                    ok, err = await loop.run_in_executor(
                        None, mt.retag_meeting, msg.get("stamp"), msg.get("assignments"), msg.get("rename"))
                except Exception as e:  # noqa: BLE001
                    engine.record_error("meeting", f"meeting_retag: {e}")
                    ok, err = False, str(e)
                await send({"type": "meeting_retag", "ok": ok, "error": err})
            elif kind == "start":
                mode = msg.get("mode", "clean")
                sid = msg.get("id")
                session = DictationSession(engine.asr, on_partial=partial_cb)
            elif kind == "audio" and session is not None:
                pcm = np.frombuffer(base64.b64decode(msg["pcm16"]), dtype="<i2")
                session.add_audio(pcm.astype(np.float32) / 32768.0)
            elif kind == "cancel":
                session = None
            elif kind == "stop" and session is not None:
                s, session = session, None
                t = time.perf_counter()
                try:
                    segs = await loop.run_in_executor(None, s.finish)
                    raw = " ".join(segs)
                    text = await loop.run_in_executor(None, engine.clean, raw, mode) if raw else ""
                    ms = int((time.perf_counter() - t) * 1000)
                    engine.counts["dictations"] += 1
                    await send({"type": "final", "text": text, "raw": raw, "ms": ms, "id": sid})
                    log(f"final {ms} ms | raw: {raw!r} | out: {text!r}")
                except Exception as e:  # noqa: BLE001
                    engine.record_error("asr", str(e))
                    await send({"type": "error", "message": str(e), "id": sid})
                    log("error", e)
            elif kind == "meeting_start":
                from engine.meeting import MeetingSession

                if meeting is None:
                    meeting = MeetingSession(engine.asr, engine.vocab, use_system=bool(msg.get("system", True)))
                    log("meeting started", meeting.name)
                await send({"type": "meeting_status", "state": "recording"})
            elif kind == "meeting_audio" and meeting is not None:
                pcm = np.frombuffer(base64.b64decode(msg["pcm16"]), dtype="<i2")
                meeting.add_audio(msg.get("source", "mic"), pcm.astype(np.float32) / 32768.0)
            elif kind == "meeting_speaker" and meeting is not None:
                meeting.set_speaker(msg.get("name", ""))
            elif kind == "meeting_stop" and meeting is not None:
                m, meeting = meeting, None
                await send({"type": "meeting_status", "state": "transcribing"})
                loop.run_in_executor(None, finalize_meeting, m)
            elif kind == "word":
                fix = None
                if engine.autocorrect:
                    try:
                        # Off the event loop: layer 3 runs a model (~10-20 ms per word).
                        fix = await loop.run_in_executor(None, engine.autocorrect.fix,
                                                         msg.get("word", ""), msg.get("before", ""))
                    except Exception as e:  # noqa: BLE001
                        log("autocorrect error", e)
                        engine.record_error("context" if engine.autocorrect.context else "server",
                                            f"autocorrect: {e}")
                if fix:
                    engine.counts["fixes"] += 1
                await send({"type": "word_result", "word": msg.get("word"), "fix": fix})
            elif kind == "feedback":
                await loop.run_in_executor(None, write_feedback, dict(msg))
    finally:
        if meeting is not None:  # app vanished mid-meeting: still save what was heard
            threading.Thread(target=lambda m=meeting: finalize_meeting(m), daemon=True).start()
        writer.close()
        log("client disconnected")


async def main():
    engine = Engine()
    server = await asyncio.start_server(lambda r, w: handle(engine, r, w), HOST, PORT, limit=2**22)
    log(f"listening on {HOST}:{PORT}")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    import signal

    # Worker threads (ASR, editor load) can keep the process alive after Ctrl-C.
    # Exit hard on SIGINT or SIGTERM so Ctrl-C and launchd stop it right away.
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: os._exit(0))
    asyncio.run(main())
