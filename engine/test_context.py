"""Eval and unit checks for autocorrect layer 3, the context check.

Run:  .venv/bin/python engine/test_context.py           (eval, ~5 min on CPU)
      .venv/bin/python -m pytest engine/test_context.py (unit checks; skipped without the model)
Both also run from engine/test_autocorrect.py.

The eval feeds exactly what the app sends: (word, up to 200 chars before it), from
the user's messages in data/raw/user_messages.jsonl, the way test_autocorrect.contexts() does.
  (a) every confusion-set word in the user's messages, unchanged. Any fix is a false fix.
      Messages are split in half by a fixed seed: the margin sweep is read on the tune
      half, the shipped margin is reported on the held-out half.
  (b) the same spots with the word swapped for each other word of its set. A fix back to
      the original is a catch (recall); precision = right fixes / all fixes.
  (c) the full false-fix pass of test_autocorrect over every correct-word token, now with
      layer 3 on.
  Latency and added memory are measured in a fresh process.
Writes the section "Context check (layer 3)" of data/autocorrect_results.md (word pairs
and numbers only) and data/raw/context_flags.md (gitignored: flagged spots with context,
for a human to check whether the user's own text had a real mistake there).
"""
import collections
import json
import os
import random
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_typo_map as B  # noqa: E402
from autocorrect import CONTEXT_DIR, CONTEXT_MARGIN, Autocorrect, pick_context  # noqa: E402

DATA = B.DATA
FULL_MAP = os.path.join(DATA, "typo_map.json")
VOCAB = os.path.join(DATA, "vocab.txt")
RESULTS = os.path.join(DATA, "autocorrect_results.md")
FLAGS = os.path.join(DATA, "raw", "context_flags.md")
CTX = os.environ.get("CONTEXT_DIR", CONTEXT_DIR)
SECTION = "## Context check (layer 3)"
SWEEP = [3.0, 4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0, 8.0]

_AC = None


def _ac():
    global _AC
    if _AC is None:
        _AC = Autocorrect(FULL_MAP, VOCAB, context_dir=CTX, context_wait=True)
    return _AC


def _need_model():
    import pytest

    if _ac().context is None:
        pytest.skip(f"no context model in {CTX} ({_ac().context_error})")


# ---------- unit checks ----------

def test_clear_context_fixes():
    _need_model()
    ac = _ac()
    assert ac.fix("weather", "I don't know ") == "whether"
    assert ac.fix("of", "turn it ") == "off"
    assert ac.fix("you're", "Thanks for ") == "your"
    assert ac.fix("it's", "The company and ") == "its"


def test_better_then():
    _need_model()
    # "better then": than leads by ~4.7 nats, under the shipped 6.0 margin, so the shipped
    # layer leaves it; it must never pick a wrong word. A looser margin does fix it.
    assert _ac().fix("then", "This one is a lot better ") in (None, "than")
    loose = Autocorrect(FULL_MAP, VOCAB, context_dir=None, context_margin=4.0)
    loose.context = _ac().context
    assert loose.fix("then", "This one is a lot better ") == "than"
    assert loose.fix("form", "I got an email ") == "from"


def test_unsure_left_context_is_left_alone():
    _need_model()
    ac = _ac()
    # "I think there going": the word after is not typed yet, so no confident call.
    assert ac.fix("there", "I think ") in (None, "they're")
    for word, before in [("then", "and "), ("their", "I saw "), ("its", ""), ("to", ""),
                         ("form", "Fill out the "), ("too", "me "),
                         ("Knew", ""), ("threw", ""), ("Passed", " ")]:
        assert ac.fix(word, before) is None, word


def test_correct_words_stay():
    _need_model()
    ac = _ac()
    assert ac.fix("than", "This one is a lot better ") is None
    assert ac.fix("from", "I got an email ") is None
    assert ac.fix("their", "They forgot ") is None


def test_case_apostrophe_and_never():
    _need_model()
    ac = _ac()
    assert ac.fix("Of", "Turn it ") == "Off"
    assert ac.fix("it’s", "The company and ") == "its"
    assert ac.fix("WEATHER", "I don't know ") is None  # all caps is skipped
    ac._never.add("weather")
    try:
        assert ac.fix("weather", "I don't know ") is None
    finally:
        ac._never.discard("weather")


def test_layer_is_optional():
    off = Autocorrect(FULL_MAP, VOCAB, context_dir=None)
    assert off.context is None
    assert off.fix("form", "I got an email ") is None
    missing = Autocorrect(FULL_MAP, VOCAB, context_dir="/nonexistent/context", context_wait=True)
    assert missing.context is None and missing.fix("form", "I got an email ") is None
    assert off.fix("teh", "") == "the"


# ---------- eval ----------

def _occurrences(msgs, ac):
    out = []  # (message index, token, before)
    for i, text in enumerate(msgs):
        for tok, before in B.tokens_of(text):
            low = tok.lower()
            if low in ac._confusion and not any(c.isupper() for c in tok[1:]):
                out.append((i, tok, before[-200:]))
    return out


def _latency(samples):
    """fix() timing and RSS in a fresh process. Samples go through stdin, never to disk."""
    code = r"""
import json, resource, sys, time
HERE, MAP, VOCAB, CTX = sys.argv[1:5]
sys.path.insert(0, HERE)
from autocorrect import Autocorrect
rss = lambda: resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6
base = rss()
ac = Autocorrect(MAP, VOCAB, context_dir=None)
no_ctx = rss()
t = time.perf_counter()
ac._load_context(CTX)  # same instance, so only layer 3 is added
load = time.perf_counter() - t
assert ac.context is not None, ac.context_error
with_ctx = rss()
samples = json.load(sys.stdin)
for w, b in samples[:20]:
    ac.fix(w, b)
ts = []
for w, b in samples:
    t = time.perf_counter()
    ac.fix(w, b)
    ts.append((time.perf_counter() - t) * 1000)
ts.sort()
print(json.dumps({"base": base, "no_ctx": no_ctx, "with_ctx": with_ctx, "after": rss(), "load": load,
                  "p50": ts[len(ts) // 2], "p95": ts[int(0.95 * len(ts))], "max": ts[-1], "n": len(ts)}))
"""
    out = subprocess.run([sys.executable, "-c", code, HERE, FULL_MAP, VOCAB, CTX],
                         input=json.dumps(samples),
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def main():
    ac = _ac()
    if ac.context is None:
        print(f"No context model in {CTX}: {ac.context_error}. Run engine/export_context.py.")
        return
    msgs = B.load_messages()
    occ = _occurrences(msgs, ac)
    ids = sorted({i for i, _, _ in occ})
    rng = random.Random(7)
    rng.shuffle(ids)
    tune = set(ids[: len(ids) // 2])
    rows = []  # (tune?, msg, tok, before, scores)
    for i, tok, before in occ:
        s = ac.context_scores(tok, before)
        if s is not None:
            rows.append((i in tune, i, tok, before, s))

    def unchanged(part, m):
        r = [x for x in rows if x[0] == part]
        return [x for x in r if pick_context(x[4], x[2].lower(), m) is not None], len(r)

    def swapped(part, m, per_set=None):
        n = caught = right = 0
        for t, _, tok, before, s in rows:
            if t != part:
                continue
            low = tok.lower()
            for w in s:
                if w == low:
                    continue
                spell = ac._spell(w, before) if "'" not in w else None  # layers 1-2 see it first
                pred = spell.lower() if spell else pick_context(s, w, m)
                n += 1
                caught += pred is not None
                right += pred == low
                if per_set is not None:
                    k = f"{w} -> {low}"
                    per_set[k][0] += 1
                    per_set[k][1] += pred == low
                    per_set[k][2] += pred is not None and pred != low
        return n, caught, right

    sweep = ["| Margin | Tune: false fixes | Held out: false fixes | Held out: recall | Held out: precision |",
             "|---|---|---|---|---|"]
    for m in SWEEP:
        ff_t, n_t = unchanged(True, m)
        ff_h, n_h = unchanged(False, m)
        n, c, r = swapped(False, m)
        mark = " (shipped)" if m == CONTEXT_MARGIN else ""
        sweep.append(f"| {m}{mark} | {len(ff_t)}/{n_t} | {len(ff_h)}/{n_h} | {r / n:.1%} | "
                     f"{r / max(c, 1):.1%} ({r}/{c}) |")

    m = CONTEXT_MARGIN
    ff_t, n_t = unchanged(True, m)
    ff_h, n_h = unchanged(False, m)
    per = collections.defaultdict(lambda: [0, 0, 0])
    n_sw, c_sw, r_sw = swapped(False, m, per)
    n_all, c_all, r_all = swapped(True, m)
    n_all, c_all, r_all = n_all + n_sw, c_all + c_sw, r_all + r_sw

    # (c) full false-fix pass, every correct-word token, real fix() with layer 3 on
    with open(FULL_MAP, encoding="utf-8") as f:
        labels = {k.lower() for k in json.load(f)}
    pool = [(tok, before[-200:]) for text in msgs for tok, before in B.tokens_of(text)
            if tok.lower() not in labels and len(tok) >= 2]
    spell_fixes = ctx_fixes = 0
    for tok, before in pool:
        out = ac.fix(tok, before)
        if out is not None:
            if ac._spell(tok, before) is not None:
                spell_fixes += 1
            else:
                ctx_fixes += 1

    lat_samples = [(tok, before) for _, _, tok, before, _ in rng.sample(rows, min(600, len(rows)))]
    lat = _latency(lat_samples)
    size = sum(os.path.getsize(os.path.join(CTX, f)) for f in os.listdir(CTX)
               if f.startswith("model.int8.onnx")) / 1e6

    flagged = ff_t + ff_h
    pairs = collections.Counter(f"{x[2]} -> {pick_context(x[4], x[2].lower(), m)}" for x in flagged)
    top_sets = sorted(per.items(), key=lambda kv: -kv[1][0])
    lines = [
        SECTION,
        "",
        "Model: distilroberta-base masked LM, int8 ONNX (onnxruntime, 2 threads, no torch at runtime),",
        f"output head cut to a {len(ac.context.col)}-token vocabulary subset. "
        f"Files: {size:.0f} MB (`.models/context/model.int8.onnx` + `.data`, "
        "from `engine/export_context.py`).",
        "Compared on the same occurrences: electra-small discriminator (14 MB, scores the written-in",
        "word as original vs replaced) reached under 2% recall before its false fixes hit 0, so the",
        "masked LM won. Trailing masks after the slot: 0 and 1 were clearly worse than 2-4.",
        "Scoring: mask at the end of the left context plus 3 trailing masks; multi-token words",
        "(they're, it's) scored left to right one mask at a time. Fix only if another word of the",
        f"set beats the typed word by the margin. Shipped margin: {m} nats (`CONTEXT_MARGIN`).",
        f"Confusion sets: `engine/confusion_sets.txt`, {len(ac._confusion)} words.",
        f"Occurrences in the user's messages: {len(rows)} (tune half {n_t}, held-out half {n_h}).",
        "Caveat: the message cleaner cuts code spans, URLs and paths, which leaves gaps the real",
        "keystroke buffer would not have (\"run [gap] are\"), and some messages are pasted",
        "prompts. False-fix counts in (a) are an upper bound for that reason.",
        "",
        "| Measure | Result |",
        "|---|---|",
        f"| (a) False fixes on real, unchanged confusion words, held out | {len(ff_h)}/{n_h} ({len(ff_h) / n_h:.3%}) |",
        f"| (a) Same, tune half | {len(ff_t)}/{n_t} ({len(ff_t) / n_t:.3%}) |",
        f"| (b) Recall on swapped words, held out | {r_sw / n_sw:.1%} ({r_sw}/{n_sw}) |",
        f"| (b) Precision on swapped words, held out | {r_sw / max(c_sw, 1):.1%} ({r_sw}/{c_sw}) |",
        f"| (b) Recall / precision, all occurrences | {r_all / n_all:.1%} / {r_all / max(c_all, 1):.1%} |",
        f"| (c) False fixes, all {len(pool)} correct-word tokens (full map) | "
        f"{(spell_fixes + ctx_fixes) / len(pool):.3%} ({spell_fixes} layers 1-2 + {ctx_fixes} layer 3) |",
        f"| fix() on confusion words, fresh process, CPU | p50 {lat['p50']:.1f} ms, p95 {lat['p95']:.1f} ms, "
        f"max {lat['max']:.1f} ms ({lat['n']} calls) |",
        f"| Model load (background thread) | {lat['load']:.2f} s |",
        f"| Peak RSS | {lat['no_ctx']:.0f} MB without layer 3, {lat['after']:.0f} MB with it "
        f"(+{lat['after'] - lat['no_ctx']:.0f} MB) |",
        "",
        "Margin sweep (held-out recall and precision on swapped words):",
        "",
        *sweep,
        "",
        "Swapped-word recall by direction, held out (wrong = fixed to a third word):",
        "",
        "| Typed -> meant | Spots | Caught | Wrong |",
        "|---|---|---|---|",
        *[f"| {k} | {v[0]} | {v[1]} | {v[2]} |" for k, v in top_sets],
        "",
        f"Flagged in the user's unchanged text at the shipped margin ({len(flagged)}). Context for each is in",
        "`data/raw/context_flags.md` (gitignored) for a human to judge: real catch or false fix.",
        "",
        *([f"- `{p}` x{n}" for p, n in pairs.most_common()] or ["- none"]),
    ]
    section = "\n".join(lines) + "\n"
    old = open(RESULTS, encoding="utf-8").read() if os.path.exists(RESULTS) else ""
    if SECTION in old:
        old = old[: old.index(SECTION)]
    with open(RESULTS, "w", encoding="utf-8") as f:
        f.write(old.rstrip("\n") + "\n\n" + section)
    os.makedirs(os.path.dirname(FLAGS), exist_ok=True)
    with open(FLAGS, "w", encoding="utf-8") as f:
        f.write(f"# Layer 3 flags in the user's unchanged messages (margin {m})\n\n")
        for t, _, tok, before, s in flagged:
            best = pick_context(s, tok.lower(), m)
            f.write(f"- {'tune' if t else 'held'} | lead {s[best] - s[tok.lower()]:.1f} | "
                    f"`{tok}` -> `{best}` | ...{before[-90:]!r}\n")
    print(section)


if __name__ == "__main__":
    main()
