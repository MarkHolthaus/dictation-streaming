"""Held-out evaluation for engine/autocorrect.py, plus a few unit checks.

Run:  .venv/bin/python engine/test_autocorrect.py     (eval, writes data/autocorrect_results.md)
      .venv/bin/python -m pytest engine/test_autocorrect.py   (unit checks)

Split: 20% of your messages are held out by a fixed seed. The typo map and
bigrams are rebuilt from the other 80% only. Labels are the reviewed full map
in data/typo_map.json. Precision and recall are measured on the held-out 20%.
False-fix rate is measured on 2000 sampled correct words from all messages.
These numbers are for layers 1-2 (typo map, symspell); layer 3 is off here.

Layer 3 (context check) has its own eval and unit checks in engine/test_context.py.
Both run from here: main() runs its eval after this one, and pytest collects its
unit checks through the import below.
"""
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_typo_map as B  # noqa: E402
from autocorrect import Autocorrect  # noqa: E402
import test_context  # noqa: E402
from test_context import (  # noqa: E402,F401  layer 3 unit checks, collected by pytest
    test_better_then,
    test_case_apostrophe_and_never,
    test_clear_context_fixes,
    test_correct_words_stay,
    test_layer_is_optional,
    test_unsure_left_context_is_left_alone,
)

DATA = B.DATA
FULL_MAP = os.path.join(DATA, "typo_map.json")
VOCAB = os.path.join(DATA, "vocab.txt")
RESULTS = os.path.join(DATA, "autocorrect_results.md")


def contexts(text):
    """(word, text before the word) for each word in a message."""
    for tok, before in B.tokens_of(text):
        yield tok, before[-200:]


def _ac():
    return Autocorrect(FULL_MAP, VOCAB, context_dir=None)


def test_typo_fixes_and_case():
    ac = _ac()
    assert ac.fix("teh", "") == "the"
    assert ac.fix("Teh", "") == "The"
    assert ac.fix("recieve", "") == "receive"


def test_never_changes_dictionary_words_or_skipped_tokens():
    ac = _ac()
    for w in ["there", "their", "Python", "ALLCAPS", "Zorp", "Acme2Go", "ollama",
              "x", "whirlpool", "cobbler", "https", "npm"]:
        assert ac.fix(w, "some text") is None, w


def _load_stats_in_fresh_process(typo_map, vocab):
    code = (
        "import sys, time, resource\n"
        f"sys.path.insert(0, {HERE!r})\n"
        "base = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n"
        "from autocorrect import Autocorrect\n"
        "t = time.perf_counter()\n"
        f"Autocorrect({typo_map!r}, {vocab!r}, context_dir=None)\n"
        "load = time.perf_counter() - t\n"
        "peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n"
        "print(load, base, peak)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    load, base, peak = out.stdout.split()
    return float(load), float(base) / 1e6, float(peak) / 1e6


def main():
    msgs = B.load_messages()
    rng = random.Random(42)
    order = list(range(len(msgs)))
    rng.shuffle(order)
    held = set(order[: round(0.2 * len(msgs))])
    train = [m for i, m in enumerate(msgs) if i not in held]
    test = [m for i, m in enumerate(msgs) if i in held]

    ss = B.load_symspell()
    dictionary = B.build_dictionary(ss)
    vocab = B.build_vocab()
    train_map = B.build_map(train, ss, dictionary, vocab)
    with open(FULL_MAP, encoding="utf-8") as f:
        labels = {k.lower(): v for k, v in json.load(f).items()}

    tmp = tempfile.mkdtemp(prefix="autocorrect-eval-")
    typo_path = os.path.join(tmp, "typo_map.json")
    vocab_path = os.path.join(tmp, "vocab.txt")
    with open(typo_path, "w", encoding="utf-8") as f:
        json.dump({k: v[0] for k, v in train_map.items()}, f, ensure_ascii=False)
    with open(vocab_path, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(vocab)) + "\n")
    shutil.copy(os.path.join(DATA, "dictionary.txt"), tmp)
    shutil.copy(os.path.join(DATA, "symspell_freq.txt"), tmp)
    if os.path.exists(os.path.join(DATA, "seen.txt")):
        shutil.copy(os.path.join(DATA, "seen.txt"), tmp)
    shutil.copy(os.path.join(DATA, "known_words.txt"), tmp)
    with open(os.path.join(tmp, "bigrams.json"), "w", encoding="utf-8") as f:
        json.dump(B.build_bigrams(train), f, ensure_ascii=False)

    ac = Autocorrect(typo_path, vocab_path, context_dir=None)

    occurrences = hits = predicted = correct_preds = calls = 0
    total_time = 0.0
    for text in test:
        for tok, before in contexts(text):
            label = labels.get(tok.lower())
            t0 = time.perf_counter()
            pred = ac.fix(tok, before)
            total_time += time.perf_counter() - t0
            calls += 1
            matches = pred is not None and label is not None and pred.lower() == label.lower()
            if label is not None:
                occurrences += 1
                hits += matches
            if pred is not None:
                predicted += 1
                correct_preds += matches
    precision = correct_preds / predicted if predicted else 0.0
    recall = hits / occurrences if occurrences else 0.0

    pool = [(tok, before) for text in msgs for tok, before in contexts(text)
            if tok.lower() not in labels and len(tok) >= 2]
    sample = rng.sample(pool, min(2000, len(pool)))
    false_fixes = []
    for tok, before in sample:
        pred = ac.fix(tok, before)
        if pred is not None:
            false_fixes.append((tok, pred))
    false_rate = len(false_fixes) / len(sample)
    full_fixes = sum(1 for tok, before in pool if ac.fix(tok, before) is not None)
    full_rate = full_fixes / len(pool)

    load_s, base_mb, peak_mb = _load_stats_in_fresh_process(FULL_MAP, VOCAB)
    ms_per_call = total_time / calls * 1000
    shutil.rmtree(tmp, ignore_errors=True)

    lines = [
        "# Autocorrect results",
        "",
        f"Messages: {len(msgs)} (train {len(train)}, held out {len(test)}).",
        f"Typo map: {len(train_map)} entries from the train split, {len(labels)} in the full map.",
        "",
        "| Measure | Result |",
        "|---|---|",
        f"| Held-out typo occurrences | {occurrences} |",
        f"| Precision on held-out words | {precision:.1%} ({correct_preds}/{predicted} fixes right) |",
        f"| Recall on held-out typos | {recall:.1%} ({hits}/{occurrences}) |",
        f"| False-fix rate, 2000 random correct words | {false_rate:.2%} ({len(false_fixes)}/{len(sample)}) |",
        f"| False-fix rate, all {len(pool)} correct-word tokens | {full_rate:.2%} ({full_fixes}) |",
        f"| Mean fix() time, {calls} calls | {ms_per_call:.3f} ms |",
        f"| Load time (fresh process) | {load_s:.2f} s |",
        f"| Peak RSS after load | {peak_mb:.0f} MB (bare Python: {base_mb:.0f} MB) |",
        "",
        "False fixes in the sample (first 20):",
        "",
    ]
    lines += [f"- `{t}` -> `{p}`" for t, p in false_fixes[:20]] or ["- none"]
    text = "\n".join(lines) + "\n"
    with open(RESULTS, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    test_context.main()  # appends the "Context check (layer 3)" section


if __name__ == "__main__":
    main()
