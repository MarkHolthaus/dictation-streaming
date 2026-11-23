"""Turn the hand-marked sources into jsonl. ~~x~~ = delete, <<x>> = edit, rest = keep.

eval_src.txt -> eval_set.jsonl (tuned against while building)
eval_holdout_src.txt -> eval_holdout.jsonl (written late, never used to tune)
"""
import json, os, re
here = os.path.dirname(os.path.abspath(__file__))
for src, dst in (("eval_src.txt", "eval_set.jsonl"), ("eval_holdout_src.txt", "eval_holdout.jsonl"), ("eval_xsent_src.txt", "eval_xsent.jsonl")):
    out = []
    for i, line in enumerate(open(os.path.join(here, src)).read().strip().split("\n")):
        words, labels = [], []
        for part in re.split(r"(~~.*?~~|<<.*?>>)", line):
            if not part.strip():
                continue
            if part.startswith("~~"):
                lab, body = "D", part[2:-2]
            elif part.startswith("<<"):
                lab, body = "E", part[2:-2]
            else:
                lab, body = "K", part
            for w in body.split():
                words.append(w); labels.append(lab)
        out.append({"id": i + 1, "text": " ".join(words), "words": words, "labels": labels})
    with open(os.path.join(here, dst), "w") as f:
        for r in out:
            f.write(json.dumps(r) + "\n")
    print(dst, len(out), "items")
