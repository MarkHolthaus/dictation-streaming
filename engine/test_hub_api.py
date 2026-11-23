"""Hub API: status, meeting_list, meeting_get, meeting_retag. Fake meeting folders in a temp dir.

    HF_HUB_OFFLINE=1 .venv/bin/python -m pytest -q -p no:cacheprovider engine/test_hub_api.py
"""

import asyncio
import json
import os

import pytest

from engine import meeting as mt
from engine import server


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    raw, vault = tmp_path / "raw", tmp_path / "vault"
    raw.mkdir()
    vault.mkdir()
    monkeypatch.setattr(mt, "RAW_DIR", str(raw))
    monkeypatch.setattr(mt, "VAULT_DIR", str(vault))
    return raw, vault


SEGS = [
    {"t": 0.0, "end": 4.0, "source": "mic", "speaker": "Pat", "text": "Hi, how are you?"},
    {"t": 5.0, "end": 9.0, "source": "system", "speaker": "Them", "text": "Good, thanks."},
    {"t": 10.0, "end": 14.0, "source": "system", "speaker": "Dave", "text": "Ready to start."},
    {"t": 15.0, "end": 19.0, "source": "system", "speaker": "Them", "text": "Let's begin."},
]


def make_meeting(raw, vault, stamp="2026-11-02_1430", title="Budget sync", segs=SEGS, notes=True):
    name = stamp.replace("_", " ")
    d = raw / name
    d.mkdir()
    (d / "mic.wav").write_bytes(b"x")
    (d / "transcript.json").write_text(json.dumps(
        {"started": f"{stamp[:10]}T{stamp[11:13]}:{stamp[13:]}:00", "duration": 125.4,
         "segments": [dict(s) for s in segs]}))
    if notes:
        (vault / f"{name} Meeting.md").write_text(f"---\ntype: meeting\n---\n\n# {title}\n\n## Summary\n\nStuff.\n")
    started = mt.datetime.fromisoformat(f"{stamp[:10]}T{stamp[11:13]}:{stamp[13:]}:00")
    (vault / f"{name} Meeting transcript.md").write_text(
        mt.render_transcript_note(name, started, 125.4, mt.merge_turns(segs), f"{name} Meeting"))
    return d


def test_list_newest_first_and_untagged(dirs):
    raw, vault = dirs
    make_meeting(raw, vault)
    make_meeting(raw, vault, stamp="2026-11-03_0900", title="Later")
    (raw / "2026-11-04 1000").mkdir()  # in progress: no transcript.json yet
    (raw / "stray").mkdir()
    items = mt.list_meetings()
    assert [m["stamp"] for m in items] == ["2026-11-03_0900", "2026-11-02_1430"]
    m = items[1]
    assert m["title"] == "Budget sync"
    assert m["duration_s"] == 125
    assert m["speakers"] == ["Dave", "Pat", "Them"]
    assert m["untagged"] == 2
    assert m["date"] == "2026-11-02T14:30:00"
    assert m["notes"].endswith("2026-11-02 1430 Meeting.md") and os.path.exists(m["notes"])
    assert m["transcript"].endswith("Meeting transcript.md")


def test_list_without_notes_or_dirs(dirs, tmp_path, monkeypatch):
    raw, vault = dirs
    make_meeting(raw, vault, notes=False)
    m = mt.list_meetings()[0]
    assert m["title"] == "Meeting" and m["notes"] == ""
    monkeypatch.setattr(mt, "RAW_DIR", str(tmp_path / "nope"))
    assert mt.list_meetings() == []


def test_get(dirs):
    raw, vault = dirs
    make_meeting(raw, vault)
    r = mt.get_meeting("2026-11-02_1430")
    assert r["segments"][1] == {"i": 1, "start": 5.0, "end": 9.0, "source": "system",
                                "speaker": "Them", "text": "Good, thanks."}
    assert "# Budget sync" in r["summary"]
    miss = mt.get_meeting("2026-11-09_1111")
    assert miss["segments"] == [] and miss["error"]
    assert mt.get_meeting("../x")["segments"] == []


def test_retag_assign_then_rename(dirs):
    raw, vault = dirs
    d = make_meeting(raw, vault)
    notes_before = (vault / "2026-11-02 1430 Meeting.md").read_text()
    ok, err = mt.retag_meeting("2026-11-02_1430", [{"i": 1, "speaker": "Sam"}], {"Them": "Priya"})
    assert (ok, err) == (True, "")
    data = json.load(open(d / "transcript.json"))
    assert [s["speaker"] for s in data["segments"]] == ["Pat", "Sam", "Dave", "Priya"]
    assert [s["text"] for s in data["segments"]] == [s["text"] for s in SEGS]  # words untouched
    note = (vault / "2026-11-02 1430 Meeting transcript.md").read_text()
    assert "**Sam** [00:05]: Good, thanks." in note and "**Priya** [00:15]" in note
    assert "Them" not in note and "speakers: [Dave, Pat, Priya, Sam]" in note
    assert (vault / "2026-11-02 1430 Meeting.md").read_text() == notes_before  # summary not redone
    assert not [f for f in os.listdir(d) if f.endswith(".tmp")]
    assert mt.list_meetings()[0]["untagged"] == 0


def test_retag_rejects(dirs):
    raw, vault = dirs
    d = make_meeting(raw, vault)
    before = (d / "transcript.json").read_text()
    for stamp in ("../2026-11-02_1430", "2026-11-02_1430/..", "/etc", "", None, "2026-11-02_1430\0"):
        assert mt.retag_meeting(stamp, [], {})[0] is False
    assert mt.retag_meeting("2026-11-09_1111", [], {})[0] is False
    assert mt.retag_meeting("2026-11-02_1430", [{"i": 99, "speaker": "A"}], {})[0] is False
    assert mt.retag_meeting("2026-11-02_1430", [{"i": 0, "speaker": " "}], {})[0] is False
    assert mt.retag_meeting("2026-11-02_1430", [], {"Them": ""})[0] is False
    assert (d / "transcript.json").read_text() == before


def test_retag_without_vault_note(dirs):
    raw, vault = dirs
    d = make_meeting(raw, vault)
    os.remove(vault / "2026-11-02 1430 Meeting transcript.md")
    assert mt.retag_meeting("2026-11-02_1430", [], {"Them": "Z"})[0] is True
    assert not (vault / "2026-11-02 1430 Meeting transcript.md").exists()


# --- over the socket, no models loaded -----------------------------------------------------

class FakeAsr:
    bits = 8
    model_id = "fake"


class FakeEditor:
    def loaded(self):
        return False


def fake_engine():
    e = server.Engine.__new__(server.Engine)
    e.started = server.time.time() - 5
    e.errors = server.deque(maxlen=50)
    e.counts = {"dictations": 0, "fixes": 0, "meetings": 0}
    e._context_error_seen = False
    e.asr, e.tagger, e.autocorrect, e.editor = FakeAsr(), None, None, FakeEditor()
    return e


def test_socket_messages(dirs):
    raw, vault = dirs
    make_meeting(raw, vault)

    async def run():
        eng = fake_engine()
        for i in range(60):
            eng.record_error("server", f"e{i}")
        srv = await asyncio.start_server(lambda r, w: server.handle(eng, r, w), "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        r, w = await asyncio.open_connection("127.0.0.1", port)

        async def ask(obj):
            w.write((json.dumps(obj) + "\n").encode())
            await w.drain()
            return json.loads(await asyncio.wait_for(r.readline(), 5))

        st = await ask({"type": "status"})
        assert st["type"] == "status" and st["asr_bits"] == 8 and st["uptime_s"] >= 5
        assert st["models"] == {"asr": True, "tagger": False, "context": False, "editor": False}
        assert st["counts"] == {"dictations": 0, "fixes": 0, "meetings": 0}
        assert len(st["errors"]) == 50 and st["errors"][-1]["msg"] == "e59"
        assert set(st["errors"][0]) == {"t", "where", "msg"}
        lst = await ask({"type": "meeting_list"})
        assert lst["meetings"][0]["stamp"] == "2026-11-02_1430"
        got = await ask({"type": "meeting_get", "stamp": "2026-11-02_1430"})
        assert got["type"] == "meeting_get" and len(got["segments"]) == 4
        bad = await ask({"type": "meeting_retag", "stamp": "../x", "assignments": [], "rename": {}})
        assert bad == {"type": "meeting_retag", "ok": False, "error": "bad stamp"}
        ok = await ask({"type": "meeting_retag", "stamp": "2026-11-02_1430",
                        "assignments": [{"i": 3, "speaker": "Dave"}], "rename": {"Them": "Priya"}})
        assert ok == {"type": "meeting_retag", "ok": True, "error": ""}
        w.close()
        srv.close()

    asyncio.run(run())
