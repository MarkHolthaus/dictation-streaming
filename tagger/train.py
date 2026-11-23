"""Fine-tune a small encoder for K/D/E word tagging on CPU.

python tagger/train.py --model google/electra-small-discriminator --name electra-small --epochs 3
Saves to .models/runs/<name>/ (gitignored). Labels go on the first subword only.
"""
import argparse
import json
import os
import random
import time

import numpy as np
import torch
from transformers import AutoModelForTokenClassification, AutoTokenizer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LABELS = ["K", "D", "E"]
L2I = {l: i for i, l in enumerate(LABELS)}


def load_jsonl(p):
    return [json.loads(l) for l in open(p) if l.strip()]


def encode(tok, rows, max_len=160):
    enc = tok([r["words"] for r in rows], is_split_into_words=True, truncation=True, max_length=max_len)
    out = []
    for k, r in enumerate(rows):
        wid = enc.word_ids(k)
        labels, prev = [], None
        for w in wid:
            if w is None or w == prev:
                labels.append(-100)
            else:
                labels.append(L2I[r["labels"][w]])
            prev = w
        out.append((enc["input_ids"][k], labels))
    return out


def batches(data, bs, shuffle, pad_id):
    idx = list(range(len(data)))
    if shuffle:
        random.shuffle(idx)
        chunks = [idx[i:i + bs * 50] for i in range(0, len(idx), bs * 50)]
        idx = [j for c in chunks for j in sorted(c, key=lambda j: len(data[j][0]))]
        groups = [idx[i:i + bs] for i in range(0, len(idx), bs)]
        random.shuffle(groups)
    else:
        idx.sort(key=lambda j: len(data[j][0]))
        groups = [idx[i:i + bs] for i in range(0, len(idx), bs)]
    for g in groups:
        m = max(len(data[j][0]) for j in g)
        ids = torch.full((len(g), m), pad_id)
        lab = torch.full((len(g), m), -100)
        att = torch.zeros((len(g), m), dtype=torch.long)
        for r, j in enumerate(g):
            n = len(data[j][0])
            ids[r, :n] = torch.tensor(data[j][0])
            lab[r, :n] = torch.tensor(data[j][1])
            att[r, :n] = 1
        yield g, ids, att, lab


@torch.no_grad()
def predict(model, tok, rows, bs=64):
    model.eval()
    data = encode(tok, rows)
    preds = [None] * len(rows)
    for g, ids, att, lab in batches(data, bs, False, tok.pad_token_id):
        logits = model(input_ids=ids, attention_mask=att).logits
        p = logits.argmax(-1)
        for r, j in enumerate(g):
            preds[j] = [LABELS[x] for x, y in zip(p[r].tolist(), lab[r].tolist()) if y != -100]
    # words cut by truncation default to K
    return [pr + ["K"] * (len(rows[i]["words"]) - len(pr)) for i, pr in enumerate(preds)]


def prf(rows, preds):
    res = {}
    for lab in LABELS:
        tp = fp = fn = 0
        for r, p in zip(rows, preds):
            for g, q in zip(r["labels"], p):
                tp += g == lab and q == lab
                fp += g != lab and q == lab
                fn += g == lab and q != lab
        pr = tp / (tp + fp) if tp + fp else 0.0
        rc = tp / (tp + fn) if tp + fn else 0.0
        res[lab] = (pr, rc, 2 * pr * rc / (pr + rc) if pr + rc else 0.0)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="google/electra-small-discriminator")
    ap.add_argument("--name", default="electra-small")
    ap.add_argument("--epochs", type=float, default=3)
    ap.add_argument("--bs", type=int, default=32)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--max-minutes", type=float, default=24)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--train", default=ROOT + "/data/tagger_train.jsonl")
    a = ap.parse_args()
    torch.set_num_threads(a.threads)
    random.seed(1)
    torch.manual_seed(1)
    os.environ.setdefault("HF_HOME", ROOT + "/.models/hf")

    kw = {"add_prefix_space": True} if "roberta" in a.model else {}
    tok = AutoTokenizer.from_pretrained(a.model, **kw)
    model = AutoModelForTokenClassification.from_pretrained(a.model, num_labels=3, id2label=dict(enumerate(LABELS)), label2id=L2I)
    model.float()
    print("params", sum(p.numel() for p in model.parameters()) / 1e6, "M", flush=True)

    train_rows = load_jsonl(a.train)
    if a.limit:
        train_rows = train_rows[:a.limit]
    dev_rows = load_jsonl(ROOT + "/data/tagger_dev.jsonl")
    eval_rows = load_jsonl(ROOT + "/tagger/eval_set.jsonl")
    train = encode(tok, train_rows)

    steps_per_epoch = (len(train) + a.bs - 1) // a.bs
    total = int(steps_per_epoch * a.epochs)
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=0.01)
    warm = int(0.06 * total)
    budget = a.max_minutes * 60

    def set_lr(step, elapsed):
        # progress is whichever runs out first: steps or the time budget
        p = max(step / total, elapsed / budget)
        f = min((step + 1) / max(1, warm), max(0.0, 1.0 - p) / max(1e-9, 1.0 - 0.06))
        for g in opt.param_groups:
            g["lr"] = a.lr * min(1.0, f)
        return p
    # E is rare, weight it up a little
    wts = torch.tensor([1.0, 1.0, 2.0])
    lossf = torch.nn.CrossEntropyLoss(weight=wts, ignore_index=-100)

    t0 = time.time()
    step = 0
    done = False
    ep = 0
    while not done:
        ep += 1
        model.train()
        run = 0.0
        for g, ids, att, lab in batches(train, a.bs, True, tok.pad_token_id):
            prog = set_lr(step, time.time() - t0)
            logits = model(input_ids=ids, attention_mask=att).logits
            loss = lossf(logits.view(-1, 3), lab.view(-1))
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            opt.zero_grad()
            step += 1
            run = 0.98 * run + 0.02 * loss.item() if step > 1 else loss.item()
            if step % 100 == 0:
                print(f"step {step}/{total} loss {run:.4f} {time.time() - t0:.0f}s", flush=True)
            if prog >= 1.0:
                done = True
                break
        dp = predict(model, tok, dev_rows)
        ev = predict(model, tok, eval_rows)
        d, e = prf(dev_rows, dp), prf(eval_rows, ev)
        print(f"epoch {ep} dev F1 " + " ".join(f"{l}={d[l][2]:.4f}" for l in LABELS) + " | eval F1 " + " ".join(f"{l}={e[l][2]:.4f}" for l in LABELS), flush=True)
    out = f"{ROOT}/.models/runs/{a.name}"
    os.makedirs(out, exist_ok=True)
    model.save_pretrained(out)
    tok.save_pretrained(out)
    print("saved", out, f"train time {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
