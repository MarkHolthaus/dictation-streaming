"""Words the user writes on purpose: seen 2+ times in their messages or notes folder, and not a known typo.
Runtime autocorrect never guesses on these. Writes data/seen.txt."""
import glob, json, os, re
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tok = re.compile(r"[A-Za-z]{3,}")
c = Counter()
with open(os.path.join(ROOT, "data/raw/user_messages.jsonl")) as f:
    for line in f:
        c.update(w.lower() for w in tok.findall(json.loads(line)["text"]))
NOTES = os.environ.get("DICTATION_NOTES_DIR", "")  # optional folder of markdown notes
skip = ("_to_delete", ".smart-env")
for p in (glob.glob(os.path.join(os.path.expanduser(NOTES), "**/*.md"), recursive=True) if NOTES else []):
    if any(s in p for s in skip):
        continue
    try:
        c.update(w.lower() for w in tok.findall(open(p, encoding="utf-8", errors="ignore").read()))
    except OSError:
        pass
typos = {k.lower() for k in json.load(open(os.path.join(ROOT, "data/typo_map.json")))}
seen = sorted(w for w, n in c.items() if n >= 2 and w not in typos)
open(os.path.join(ROOT, "data/seen.txt"), "w").write("\n".join(seen) + "\n")
print(len(seen), "seen words;", [w for w in ("stderr", "teh", "wokring") if w in seen])
