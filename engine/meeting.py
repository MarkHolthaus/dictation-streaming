"""Meeting tracker. Records two audio sources, labels who spoke, writes notes to a notes folder.

Live (from the app, see PROTOCOL.md "Meetings"): MeetingSession gets 16 kHz mono audio tagged
`mic` or `system`, cuts each source at pauses and transcribes while the meeting runs.
On finish it writes the transcript note first, then asks a local model for the summary.

Offline test, no app needed (run in a normal terminal, needs the GPU):
    .venv/bin/python -m engine.meeting --wav clip.wav [--speaker Sam]

Speaker rules:
- With system audio on, mic segments are the user and system segments carry the speaker the user
  has clicked (default "Them").
- Mic only: every segment carries the clicked speaker (default: the user).
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
import wave
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import numpy as np

from engine.asr import ASR, SR, Segmenter, has_speech

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ME = os.environ.get("MEETING_ME", "Me")
VAULT_DIR = os.path.expanduser(os.environ.get("MEETING_VAULT_DIR", "~/Documents/Dictation Meetings"))
RAW_DIR = os.path.join(ROOT, "data", "raw", "meetings")  # gitignored
OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
SUMMARY_MODEL = os.environ.get("MEETING_MODEL", "qwen2.5:14b")
FALLBACK_MODEL = os.environ.get("MEETING_FALLBACK_MODEL", "qwen2.5vl:7b")  # smaller, if 14b fails (memory)
CHUNK_CHARS = 12000  # transcript text per summary call


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), "[meeting]", *a, flush=True)


# The server sets this so caught failures show up in the status error list: fn(where, msg).
error_hook = None


def _report(msg: str) -> None:
    if error_hook:
        error_hook("meeting", msg)


def stamp_time(t: float) -> str:
    t = int(t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class _Source:
    def __init__(self, name: str, wav_path: str):
        self.name = name
        self.seg = Segmenter()
        self.samples = 0  # samples received
        self.cut = 0  # samples already handed out as segments
        self.wav = wave.open(wav_path, "wb")
        self.wav.setnchannels(1)
        self.wav.setsampwidth(2)
        self.wav.setframerate(SR)


class MeetingSession:
    def __init__(self, asr: ASR, vocab=None, use_system: bool = True, started: datetime | None = None,
                 out_dir: str | None = None, vault_dir: str | None = None):
        self.asr = asr
        self.vocab = vocab
        self.use_system = use_system
        self.started = started or datetime.now()
        self.name = self.started.strftime("%Y-%m-%d %H%M")
        self.dir = out_dir or os.path.join(RAW_DIR, self.name)
        self.vault_dir = vault_dir or VAULT_DIR
        os.makedirs(self.dir, exist_ok=True)
        self.src = {n: _Source(n, os.path.join(self.dir, f"{n}.wav")) for n in ("mic", "system")}
        self.pool = ThreadPoolExecutor(max_workers=1)  # the ASR lock serializes anyway
        self.futures: list[tuple[float, float, str, object]] = []  # start, end, source, future
        self.speaker_log: list[tuple[float, str]] = []  # (seconds since start, name)
        self.lock = threading.Lock()
        self.finished = False

    # --- live input -------------------------------------------------------------------

    def elapsed(self) -> float:
        """Meeting clock: the furthest any source has got. Audio is the clock, not wall time."""
        return max(s.samples for s in self.src.values()) / SR

    def add_audio(self, source: str, audio: np.ndarray) -> None:
        s = self.src.get(source)
        if s is None or self.finished:
            return
        if source == "system" and not self.use_system:
            return
        pcm = np.clip(audio * 32768.0, -32768, 32767).astype("<i2")
        s.wav.writeframes(pcm.tobytes())
        s.samples += len(audio)
        for seg in s.seg.push(audio):
            self._queue(s, seg)

    def set_speaker(self, name: str) -> None:
        name = name.strip()
        if name:
            with self.lock:
                self.speaker_log.append((self.elapsed(), name))

    def _queue(self, s: _Source, seg: np.ndarray) -> None:
        start = s.cut / SR
        s.cut += len(seg)
        if not has_speech(seg, s.seg.noise):
            return
        self.futures.append((start, s.cut / SR, s.name, self.pool.submit(self.asr.transcribe, seg)))

    # --- finishing --------------------------------------------------------------------

    def _speaker_at(self, t: float) -> str | None:
        who = None
        for ts, name in self.speaker_log:
            if ts <= t:
                who = name
        return who

    def _label(self, source: str, mid: float) -> str:
        clicked = self._speaker_at(mid)
        if self.use_system:
            return ME if source == "mic" else (clicked or "Them")
        return clicked or ME

    def finish(self) -> list[dict]:
        """Flush, wait for transcription, return labelled segments in time order."""
        self.finished = True
        for s in self.src.values():
            tail = s.seg.flush()
            if len(tail):
                self._queue(s, tail)
            s.wav.close()
        segs = []
        for start, end, source, fut in self.futures:
            text = fut.result()
            if not text:
                continue
            if self.vocab:
                text = self.vocab.fix(text)
            mid = start + min(1.0, (end - start) / 2)
            segs.append({"t": round(start, 1), "end": round(end, 1), "source": source,
                         "speaker": self._label(source, mid), "text": text})
        self.pool.shutdown()
        self.asr.mx.clear_cache()
        segs.sort(key=lambda d: (d["t"], d["source"]))
        return segs

    def duration(self) -> float:
        return self.elapsed()


def _atomic_write(path: str, text: str) -> None:
    """Temp file in the same folder, then rename, so a reader never sees half a file."""
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


# --- browsing and retagging finished meetings (hub window) ----------------------------------

DEFAULT_LABEL = "Them"  # what unlabeled system audio gets
_retag_lock = threading.Lock()


def stamp_of(folder: str) -> str:
    """Folder "2026-11-02 1430" is stamp "2026-11-02_1430"."""
    return folder.replace(" ", "_")


def _folder_dir(stamp, raw_dir: str | None) -> str | None:
    """Folder for a stamp, or None if the stamp is not a plain name."""
    raw_dir = raw_dir or RAW_DIR
    if not isinstance(stamp, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}[_ ]\d{4}", stamp):
        return None
    return os.path.join(raw_dir, stamp.replace("_", " "))


def _load_transcript(d: str) -> dict | None:
    try:
        with open(os.path.join(d, "transcript.json"), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None  # missing (meeting still in progress) or unreadable
    if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
        return None
    return data


def _vault_paths(stamp: str, vault_dir: str | None) -> tuple[str, str]:
    name = stamp.replace("_", " ")
    v = vault_dir or VAULT_DIR
    return os.path.join(v, f"{name} Meeting.md"), os.path.join(v, f"{name} Meeting transcript.md")


def _title_of(notes_path: str) -> str:
    try:
        with open(notes_path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("# "):
                    return line[2:].strip() or "Meeting"
    except OSError:
        pass
    return "Meeting"


def _api_segment(i: int, s: dict) -> dict:
    return {"i": i, "start": float(s.get("t", 0)), "end": float(s.get("end", 0)),
            "source": s.get("source", "mic"), "speaker": s.get("speaker", ""), "text": s.get("text", "")}


def list_meetings(raw_dir: str | None = None, vault_dir: str | None = None) -> list[dict]:
    raw_dir = raw_dir or RAW_DIR
    out = []
    try:
        folders = os.listdir(raw_dir)
    except OSError:
        return out
    for folder in folders:
        d = _folder_dir(stamp_of(folder), raw_dir)
        if d is None or not os.path.isdir(d):
            continue
        data = _load_transcript(d)
        if data is None:
            continue
        stamp = stamp_of(folder)
        segs = [s for s in data["segments"] if isinstance(s, dict)]
        notes, transcript = _vault_paths(stamp, vault_dir)
        speakers = sorted({s.get("speaker", "") for s in segs if s.get("speaker")})
        date = data.get("started") or datetime.strptime(stamp, "%Y-%m-%d_%H%M").isoformat(timespec="seconds")
        out.append({
            "stamp": stamp, "title": _title_of(notes), "date": date,
            "duration_s": int(data.get("duration") or 0), "speakers": speakers,
            "notes": notes if os.path.exists(notes) else "",
            "transcript": transcript if os.path.exists(transcript) else "",
            "untagged": sum(1 for s in segs if s.get("speaker") in (DEFAULT_LABEL, "")),
        })
    out.sort(key=lambda m: m["stamp"], reverse=True)
    return out


def get_meeting(stamp, raw_dir: str | None = None, vault_dir: str | None = None) -> dict:
    d = _folder_dir(stamp, raw_dir)
    data = _load_transcript(d) if d else None
    if data is None:
        return {"stamp": stamp, "segments": [], "summary": "", "error": "meeting not found"}
    summary = ""
    try:
        with open(_vault_paths(stamp, vault_dir)[0], encoding="utf-8") as f:
            summary = f.read()
    except OSError:
        pass
    segs = [_api_segment(i, s) for i, s in enumerate(data["segments"]) if isinstance(s, dict)]
    return {"stamp": stamp, "segments": segs, "summary": summary}


def retag_meeting(stamp, assignments, rename, raw_dir: str | None = None,
                  vault_dir: str | None = None) -> tuple[bool, str]:
    """Change speaker labels in transcript.json and the vault transcript note. Never touches text
    and never re-runs the summary."""
    d = _folder_dir(stamp, raw_dir)
    if d is None:
        return False, "bad stamp"
    assignments = assignments or []
    rename = rename or {}
    if not isinstance(assignments, list) or not isinstance(rename, dict):
        return False, "bad request"
    with _retag_lock:
        data = _load_transcript(d)
        if data is None:
            return False, "meeting not found"
        segs = data["segments"]
        picks = []
        for a in assignments:
            i = a.get("i") if isinstance(a, dict) else None
            who = a.get("speaker") if isinstance(a, dict) else None
            if (not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < len(segs)
                    or not isinstance(who, str) or not who.strip()):
                return False, "bad assignment"
            picks.append((i, who.strip()))
        names = {}
        for old, new in rename.items():
            if not isinstance(new, str) or not new.strip():
                return False, "bad rename"
            names[old] = new.strip()
        for i, who in picks:
            segs[i]["speaker"] = who
        for s in segs:
            if s.get("speaker") in names:
                s["speaker"] = names[s["speaker"]]
        try:
            _atomic_write(os.path.join(d, "transcript.json"), json.dumps(data, indent=1))
            notes, tpath = _vault_paths(stamp, vault_dir)
            if os.path.exists(tpath):
                started = datetime.fromisoformat(data["started"]) if data.get("started") else \
                    datetime.strptime(stamp, "%Y-%m-%d_%H%M")
                turns = merge_turns([s for s in segs if isinstance(s, dict)])
                name = stamp.replace("_", " ")
                _atomic_write(tpath, render_transcript_note(name, started, float(data.get("duration") or 0),
                                                            turns, f"{name} Meeting"))
        except (OSError, ValueError) as e:
            _report(f"retag failed: {e}")
            return False, str(e)
    return True, ""


# --- notes ------------------------------------------------------------------------------

def merge_turns(segs: list[dict]) -> list[dict]:
    """Join consecutive segments by the same speaker into one turn."""
    turns: list[dict] = []
    for s in segs:
        if turns and turns[-1]["speaker"] == s["speaker"] and s["t"] - turns[-1]["end"] < 8:
            turns[-1]["text"] += " " + s["text"]
            turns[-1]["end"] = s["end"]
        else:
            turns.append(dict(s))
    return turns


def transcript_text(turns: list[dict]) -> str:
    return "\n".join(f"[{stamp_time(t['t'])}] {t['speaker']}: {t['text']}" for t in turns)


PROMPT = f"""You write meeting notes from a transcript. The transcript lines look like
"[mm:ss] Speaker: text". The user is {ME}. The transcript comes from speech recognition and may
have small errors. Use only what is in the transcript. Never invent names, dates, numbers or
tasks. If something is not said, leave it out.

Return JSON only, with these keys:
"title": 3 to 7 words naming the meeting.
"summary": 3 to 6 sentences: what was discussed and what was decided.
"notes": list of short bullet strings with the key points, decisions and facts, in order.
"todos": list of tasks that {ME} must do, as short imperative strings.
"action_items": list of objects {{"owner": name or "Unassigned", "task": string, "due": string or ""}}
for every commitment anyone made, including {ME}'s. "due" only if a date or time was said."""


def _ollama(system: str, user: str, model: str, timeout: int = 600) -> dict:
    body = json.dumps({
        "model": model, "stream": False, "format": "json", "keep_alive": "2m",
        "options": {"temperature": 0.1, "num_ctx": 8192},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(OLLAMA + "/api/chat", body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.load(r)
    return json.loads(out["message"]["content"])


def _chunks(text: str) -> list[str]:
    lines, chunks, cur, n = text.split("\n"), [], [], 0
    for ln in lines:
        if n + len(ln) > CHUNK_CHARS and cur:
            chunks.append("\n".join(cur)); cur, n = [], 0
        cur.append(ln); n += len(ln) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def _strs(v) -> list[str]:
    return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []


def _actions(v) -> list[dict]:
    out = []
    for a in v if isinstance(v, list) else []:
        if isinstance(a, dict) and str(a.get("task", "")).strip():
            out.append({"owner": str(a.get("owner") or "Unassigned").strip(),
                        "task": str(a["task"]).strip(), "due": str(a.get("due") or "").strip()})
    return out


def summarize(text: str) -> dict:
    """Long meetings go in chunks; lists are joined and one last call writes the overall summary."""
    chunks = _chunks(text)
    model = SUMMARY_MODEL
    try:
        parts = [_ollama(PROMPT, chunks[0], model)]
    except Exception as e:  # noqa: BLE001
        if not FALLBACK_MODEL or FALLBACK_MODEL == model:
            raise
        log(f"{model} failed ({e}), trying {FALLBACK_MODEL}")
        model = FALLBACK_MODEL
        parts = [_ollama(PROMPT, chunks[0], model)]
    parts += [_ollama(PROMPT, c, model) for c in chunks[1:]]
    if len(parts) == 1:
        p = parts[0]
    else:
        joined = "\n\n".join(f"Part {i+1}: {q.get('summary', '')}" for i, q in enumerate(parts))
        top = _ollama('Combine these part summaries of one meeting. Return JSON with "title" '
                      '(3 to 7 words) and "summary" (3 to 6 sentences). Use only what is given.', joined, model)
        p = {"title": top.get("title"), "summary": top.get("summary")}
    notes, todos, acts = [], [], []
    for q in parts:
        notes += _strs(q.get("notes")); todos += _strs(q.get("todos")); acts += _actions(q.get("action_items"))
    seen = set()
    todos = [t for t in todos if not (t.lower() in seen or seen.add(t.lower()))]
    return {"title": str(p.get("title") or "Meeting").strip(), "summary": str(p.get("summary") or "").strip(),
            "notes": notes, "todos": todos, "action_items": acts}


def safe_title(t: str) -> str:
    return re.sub(r"[\\/:*?\"<>|#\[\]^]", "", t).strip()[:80] or "Meeting"


def render_transcript_note(name: str, started: datetime, duration: float, turns: list[dict],
                           notes_name: str) -> str:
    speakers = sorted({t["speaker"] for t in turns})
    head = ["---", "type: meeting-transcript", f"date: {started.strftime('%Y-%m-%d')}",
            f"time: {started.strftime('%H:%M')}", f"duration: {stamp_time(duration)}",
            "speakers: [" + ", ".join(speakers) + "]", "---", "",
            f"# Meeting transcript {name}", "", f"Notes: [[{notes_name}]]", ""]
    body = [f"**{t['speaker']}** [{stamp_time(t['t'])}]: {t['text']}\n" for t in turns]
    return "\n".join(head + body)


def write_transcript_note(sess: MeetingSession, turns: list[dict], notes_name: str) -> str:
    os.makedirs(sess.vault_dir, exist_ok=True)
    path = os.path.join(sess.vault_dir, f"{sess.name} Meeting transcript.md")
    _atomic_write(path, render_transcript_note(sess.name, sess.started, sess.duration(), turns, notes_name))
    return path


def write_notes(sess: MeetingSession, turns: list[dict], transcript_name: str, s: dict | None,
                error: str = "") -> str:
    path = os.path.join(sess.vault_dir, f"{sess.name} Meeting.md")
    speakers = sorted({t["speaker"] for t in turns})
    title = safe_title(s["title"]) if s else "Meeting"
    L = ["---", "type: meeting", f"date: {sess.started.strftime('%Y-%m-%d')}",
         f"time: {sess.started.strftime('%H:%M')}", f"duration: {stamp_time(sess.duration())}",
         "speakers: [" + ", ".join(speakers) + "]", "---", "", f"# {title}", "",
         f"Transcript: [[{transcript_name}]]", ""]
    if s:
        L += ["## Summary", "", s["summary"], "", "## Notes", ""]
        L += [f"- {n}" for n in s["notes"]] or ["- (none)"]
        L += ["", "## To-dos", ""]
        L += [f"- [ ] {t}" for t in s["todos"]] or [f"- (none for {ME})"]
        L += ["", "## Action items", ""]
        L += [f"- [ ] **{a['owner']}**: {a['task']}" + (f" (due {a['due']})" if a["due"] else "")
              for a in s["action_items"]] or ["- (none)"]
    else:
        L += ["## Summary", "", f"Summary failed: {error}. The transcript is saved. "
              f"Re-run with `python -m engine.meeting --resummarize \"{sess.dir}\"`."]
    _atomic_write(path, "\n".join(L) + "\n")
    return path


def finalize(sess: MeetingSession, progress=None) -> dict:
    """Transcript note first, so a failed summary never loses the meeting."""
    t0 = time.time()
    segs = sess.finish()
    turns = merge_turns(segs)
    _atomic_write(os.path.join(sess.dir, "transcript.json"),
                  json.dumps({"started": sess.started.isoformat(timespec="seconds"),
                              "duration": sess.duration(), "segments": segs}, indent=1))
    transcript_name = f"{sess.name} Meeting transcript"
    notes_name = f"{sess.name} Meeting"
    tpath = write_transcript_note(sess, turns, notes_name)
    log(f"transcript saved: {tpath} ({len(segs)} segments, {time.time()-t0:.1f}s)")
    if not turns:
        return {"transcript": tpath, "notes": "", "ok": False, "error": "no speech found"}
    if progress:
        progress("summarizing")
    s, err = None, ""
    try:
        s = summarize(transcript_text(turns))
    except Exception as e:  # noqa: BLE001
        err = str(e)
        log("summary failed:", e)
        _report(f"summary failed: {e}")
    npath = write_notes(sess, turns, transcript_name, s, err)
    log(f"notes saved: {npath}")
    return {"transcript": tpath, "notes": npath, "ok": s is not None, "error": err,
            "title": s["title"] if s else ""}


def main():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--wav")
    ap.add_argument("--speaker", default="", help="mic-only: label for the whole clip")
    ap.add_argument("--vault", help="output folder (default: ~/Documents/Dictation Meetings)")
    ap.add_argument("--resummarize", help="data/raw/meetings/<stamp> folder with transcript.json")
    a = ap.parse_args()
    if a.resummarize:
        d = json.load(open(os.path.join(a.resummarize, "transcript.json")))
        turns = merge_turns(d["segments"])
        print(json.dumps(summarize(transcript_text(turns)), indent=1))
        return
    from engine.asr import wav_to_array
    from engine.vocab import VocabFixer

    asr = ASR()
    vocab = VocabFixer(os.path.join(ROOT, "data", "vocab.txt"))
    sess = MeetingSession(asr, vocab, use_system=False, vault_dir=a.vault)
    if a.speaker:
        sess.set_speaker(a.speaker)
    audio = wav_to_array(a.wav)
    for i in range(0, len(audio), SR // 10):
        sess.add_audio("mic", audio[i:i + SR // 10])
    print(json.dumps(finalize(sess), indent=1))


if __name__ == "__main__":
    main()
