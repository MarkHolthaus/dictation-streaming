"""Context check for typing autocorrect (layer 3). onnxruntime + tokenizers, no torch.

Scores each word in a confusion set ("then"/"than") against the text typed before it.
Left context only: the app sends the word on space or punctuation, so nothing after it
is known yet.

Masked LM (distilroberta, default): the word's slot is a <mask> at the end of the left
context, followed by three more <mask>s so the model does not assume the sentence ends
there (0, 1, 2, 3 and 4 trailing masks were compared: 2-4 tie, 0-1 are clearly worse).
A multi-token word ("they're" = " they" + "'re") is scored left to right, one mask at a
time, so every candidate is a product of next-token probabilities and they compare fairly.

ELECTRA discriminator (comparison only): the candidate is written in, and its score is
log P(original) summed over its tokens.

    c = ContextModel(".models/context")
    c.scores("this is way better ", ["then", "than"])  # {"then": -9.1, "than": -0.2}
"""
import os

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

MAX_CTX_TOKENS = 48


def _ctx(before):
    """Left context, trimmed to whole words, plus whether a space precedes the word."""
    before = before.replace("’", "'")
    if len(before) >= 200:  # cut mid-word by the app's 200-char window
        cut = before.find(" ")
        before = before[cut + 1:] if cut >= 0 else ""
    space = bool(before) and before[-1].isspace()
    return " ".join(before.split()), space


class ContextModel:
    def __init__(self, model_dir, threads=2, tail_masks=3):
        with open(os.path.join(model_dir, "kind.txt")) as f:
            self.kind = f.read().strip()
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = 1
        so.log_severity_level = 3
        so.enable_cpu_mem_arena = False  # ~7 MB less resident, same speed
        # Prepacking stays on: turning it off saves ~55 MB but doubles latency.
        self.sess = ort.InferenceSession(os.path.join(model_dir, "model.int8.onnx"), so,
                                         providers=["CPUExecutionProvider"])
        self.tok = Tokenizer.from_file(os.path.join(model_dir, "tokenizer.json"))
        if self.kind == "roberta":
            self.bos, self.eos = self.tok.token_to_id("<s>"), self.tok.token_to_id("</s>")
            self.mask, self.pad = self.tok.token_to_id("<mask>"), self.tok.token_to_id("<pad>")
            with open(os.path.join(model_dir, "subset.txt")) as f:
                self.col = {int(x): i for i, x in enumerate(f.read().split())}
        else:
            self.bos, self.eos = self.tok.token_to_id("[CLS]"), self.tok.token_to_id("[SEP]")
            self.pad = self.tok.token_to_id("[PAD]")
        self.tail = tail_masks

    def _enc(self, text):
        return self.tok.encode(text, add_special_tokens=False).ids if text else []

    def _run(self, rows, feeds_extra=None):
        n = max(len(r) for r in rows)
        ids = np.full((len(rows), n), self.pad, dtype=np.int64)
        att = np.zeros((len(rows), n), dtype=np.int64)
        for i, r in enumerate(rows):
            ids[i, :len(r)] = r
            att[i, :len(r)] = 1
        feeds = {"input_ids": ids, "attention_mask": att}
        if feeds_extra:
            feeds.update(feeds_extra)
        return self.sess.run(None, feeds)[0]

    def scores(self, before, candidates):
        """Log score per candidate (higher = fits the context better)."""
        text, space = _ctx(before)
        ctx = self._enc(text)[-MAX_CTX_TOKENS:]
        pre = " " if (text and space) else ""
        cand_ids = {c: self._enc(pre + c) for c in candidates}
        if self.kind == "roberta":
            prefixes = []  # distinct (prefix tokens) needing a next-token distribution
            index = {}
            for c, t in cand_ids.items():
                for k in range(len(t)):
                    p = tuple(t[:k])
                    if p not in index:
                        index[p] = len(prefixes)
                        prefixes.append(p)
            rows = [[self.bos] + ctx + list(p) + [self.mask] * (1 + self.tail) + [self.eos]
                    for p in prefixes]
            pos = np.array([1 + len(ctx) + len(p) for p in prefixes], dtype=np.int64)
            lp = self._run(rows, {"pos": pos})
            out = {}
            for c, t in cand_ids.items():  # a token outside the output subset scores -inf
                cols = [self.col.get(x) for x in t]
                out[c] = (float(sum(lp[index[tuple(t[:k])], cols[k]] for k in range(len(t))))
                          if None not in cols else float("-inf"))
            return out
        rows = [[self.bos] + ctx + t + [self.eos] for t in cand_ids.values()]
        logits = self._run(rows)
        out = {}
        for i, (c, t) in enumerate(cand_ids.items()):
            z = logits[i, 1 + len(ctx): 1 + len(ctx) + len(t)]
            out[c] = float(-np.logaddexp(0.0, z).sum())  # sum log(1 - sigmoid(z))
        return out
