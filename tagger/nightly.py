"""Nightly learning from the user's corrections. Run by launchd at 3:30 AM on the Mac.

1. undo_fix feedback -> data/never_fix.txt (autocorrect leaves those words alone).
2. dictation_edit feedback -> word labels by aligning raw text with the user's edit.
3. With 20+ new labeled examples: fine-tune the current tagger on base data plus
   feedback (weighted x20), export, and swap it in only if the holdout score holds.
4. Rebuild the words-the-user-writes list, then restart the engine so it loads the new files.

    .venv/bin/python tagger/nightly.py [--force]
Logs to ~/Library/Logs/dictation-nightly.log via launchd (see launchd/).
"""
import argparse
import difflib
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, ".venv/bin/python")
FEEDBACK = os.environ.get("DICTATION_FEEDBACK", os.path.join(ROOT, "data", "raw", "feedback.jsonl"))
# Test runs set DICTATION_NIGHTLY_DIR so they never touch the real state or feedback rows.
RAW = os.environ.get("DICTATION_NIGHTLY_DIR", os.path.join(ROOT, "data/raw"))
STATE = os.path.join(RAW, "nightly_state.json")
FB_ROWS = os.path.join(RAW, "feedback_tagger.jsonl")


def log(*a):
    print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


def norm(w):
    return re.sub(r"\W", "", w).lower()


def labels_from_edit(raw, edited):
    """Raw words the user removed are D, words they changed are E, the rest K."""
    rw, ew = raw.split(), edited.split()
    sm = difflib.SequenceMatcher(a=[norm(w) for w in rw], b=[norm(w) for w in ew], autojunk=False)
    labels = ["K"] * len(rw)
    for op, i1, i2, _, _ in sm.get_opcodes():
        if op == "delete":
            labels[i1:i2] = ["D"] * (i2 - i1)
        elif op == "replace":
            labels[i1:i2] = ["E"] * (i2 - i1)
    return {"words": rw, "labels": labels}


def holdout_score(model_dir):
    out = subprocess.run([PY, "tagger/test_tagger.py", "--eval", "tagger/eval_holdout.jsonl", "--model-dir", model_dir],
                         cwd=ROOT, capture_output=True, text=True).stdout
    m = re.search(r"exact match cleaned text: (\d+)/(\d+)", out)
    return int(m.group(1)) / int(m.group(2)) if m else 0.0


def restart_engine():
    if os.environ.get("DICTATION_NO_RESTART") == "1":
        log("restart skipped (test run)")
        return
    out = subprocess.run(["lsof", "-t", "-nP", "-iTCP:47321", "-sTCP:LISTEN"], capture_output=True, text=True).stdout
    for pid in out.split():
        os.kill(int(pid), signal.SIGTERM)  # launchd KeepAlive starts it again
        log("engine restarted, pid", pid)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="train even with few new examples")
    a = ap.parse_args()
    state = json.load(open(STATE)) if os.path.exists(STATE) else {"lines": 0, "trained_rows": 0}
    lines = open(FEEDBACK).read().splitlines() if os.path.exists(FEEDBACK) else []
    new = [json.loads(l) for l in lines[state["lines"]:] if l.strip()]
    log(f"{len(new)} new feedback lines")

    never_path = os.path.join(ROOT, "data/never_fix.txt")
    never = set(open(never_path).read().split()) if os.path.exists(never_path) else set()
    rows = []
    for fb in new:
        if fb.get("kind") == "undo_fix" and fb.get("word"):
            never.add(fb["word"].lower())
        elif fb.get("kind") == "dictation_edit" and fb.get("raw") and fb.get("edited"):
            rows.append(labels_from_edit(fb["raw"], fb["edited"]))
    open(never_path, "w").write("\n".join(sorted(never)) + ("\n" if never else ""))
    with open(FB_ROWS, "a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    state["lines"] = len(lines)
    total_fb = sum(1 for _ in open(FB_ROWS)) if os.path.exists(FB_ROWS) else 0
    log(f"never_fix {len(never)} words, feedback rows {total_fb}")

    if total_fb - state["trained_rows"] >= 20 or (a.force and total_fb):
        base = [l for l in open(os.path.join(ROOT, "data/tagger_train.jsonl"))]
        fb_lines = open(FB_ROWS).read().splitlines(keepends=True)
        mix = random.sample(base, min(len(base), 20000)) + fb_lines * 20
        random.shuffle(mix)
        train_path = os.path.join(RAW, "nightly_train.jsonl")
        open(train_path, "w").writelines(mix)
        cur = os.path.join(ROOT, ".models/tagger")
        before = holdout_score(cur)
        log(f"training on {len(mix)} rows, current holdout {before:.3f}")
        start = os.path.join(ROOT, ".models/runs/electra-small-final")
        subprocess.run([PY, "tagger/train.py", "--model", start, "--name", "nightly", "--epochs", "1",
                        "--train", train_path, "--max-minutes", "20"], cwd=ROOT, check=True)
        new_dir = os.path.join(ROOT, ".models/tagger_new")
        subprocess.run([PY, "tagger/export_onnx.py", "--run", "nightly", "--out", new_dir], cwd=ROOT, check=True)
        after = holdout_score(new_dir)
        log(f"new holdout {after:.3f}")
        if after >= before:
            prev = os.path.join(ROOT, ".models/tagger_prev")
            shutil.rmtree(prev, ignore_errors=True)
            os.rename(cur, prev)
            os.rename(new_dir, cur)
            log("swapped in new tagger (old one kept in .models/tagger_prev)")
        else:
            shutil.rmtree(new_dir, ignore_errors=True)
            log("kept old tagger, new one scored lower")
        state["trained_rows"] = total_fb

    subprocess.run([PY, "engine/build_seen.py"], cwd=ROOT)
    json.dump(state, open(STATE, "w"))
    restart_engine()
    log("done")


if __name__ == "__main__":
    sys.exit(main())
