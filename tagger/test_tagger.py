"""Run the hand-written eval set through the runtime tagger.

python tagger/test_tagger.py [--model-dir .models/tagger] [--show 10]
Prints word-level F1 per label, exact match on cleaned text, and ms per call.
"""
import argparse
import json
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from engine.tagger import LABELS, Tagger, render  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default=ROOT + "/.models/tagger")
    ap.add_argument("--eval", default=ROOT + "/tagger/eval_set.jsonl")
    ap.add_argument("--show", type=int, default=8)
    a = ap.parse_args()
    rows = [json.loads(l) for l in open(a.eval) if l.strip()]
    t = Tagger(a.model_dir)
    t.clean("warm up the session with one call")
    tp = {l: 0 for l in LABELS}
    fp = dict(tp)
    fn = dict(tp)
    em_text = em_both = em_edit = 0
    times, fails = [], []
    for r in rows:
        t0 = time.perf_counter()
        tagged = t.tag(r["text"])
        words = [w for w, _ in tagged]
        pred = [l for _, l in tagged]
        cleaned = render(words, pred)
        times.append((time.perf_counter() - t0) * 1000)
        gold = r["labels"]
        assert words == r["words"], r["text"]
        for g, p in zip(gold, pred):
            for l in LABELS:
                tp[l] += g == l and p == l
                fp[l] += g != l and p == l
                fn[l] += g == l and p != l
        want = render(r["words"], gold)
        ok_text = cleaned == want
        ok_edit = ("E" in pred) == ("E" in gold)
        em_text += ok_text
        em_edit += ok_edit
        em_both += ok_text and ok_edit
        if not (ok_text and ok_edit):
            wrong = sum(g != p for g, p in zip(gold, pred))
            fails.append((wrong, r, pred, cleaned, want))
    n = len(rows)
    print(f"eval items {n}, words {sum(len(r['words']) for r in rows)}")
    f1s = {}
    for l in LABELS:
        p = tp[l] / (tp[l] + fp[l]) if tp[l] + fp[l] else 0.0
        rc = tp[l] / (tp[l] + fn[l]) if tp[l] + fn[l] else 0.0
        f1s[l] = 2 * p * rc / (p + rc) if p + rc else 0.0
        print(f"  {l}: P {p:.3f} R {rc:.3f} F1 {f1s[l]:.3f}")
    print(f"exact match cleaned text: {em_text}/{n} = {em_text / n:.3f}")
    print(f"needs_edit flag correct: {em_edit}/{n} = {em_edit / n:.3f}")
    print(f"exact match text and flag: {em_both}/{n} = {em_both / n:.3f}")
    print(f"ms per call: mean {statistics.mean(times):.1f}, median {statistics.median(times):.1f}, p95 {sorted(times)[int(0.95 * n) - 1]:.1f}")
    fails.sort(key=lambda x: -x[0])
    for wrong, r, pred, got, want in fails[:a.show]:
        print("-" * 60)
        print("input :", r["text"])
        print("tags  :", " ".join(f"{w}/{p}" if p != g else w for w, p, g in zip(r["words"], pred, r["labels"])), "  (shown: predicted label where wrong)")
        print("got   :", got)
        print("wanted:", want)
    json.dump({"f1": f1s, "em_text": em_text / n, "em_both": em_both / n, "ms_mean": statistics.mean(times)}, open(os.path.join(os.environ.get("TMPDIR", "/tmp"), "last_eval.json"), "w"))


if __name__ == "__main__":
    main()
