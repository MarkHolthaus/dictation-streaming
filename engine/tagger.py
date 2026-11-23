"""Disfluency tagger runtime. onnxruntime + tokenizers only, no torch.

Labels per whitespace word: K keep, D delete, E needs edit (goes to the editor model).
    t = Tagger(".models/tagger")
    t.tag("um so we meet Tuesday, actually no, Wednesday")
    t.clean("um so we meet Tuesday, actually no, Wednesday")  # ("So we meet Wednesday", False)
"""
import os
import re

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

LABELS = ["K", "D", "E"]
ABBREV = {"a.m.", "p.m.", "e.g.", "i.e.", "etc.", "mr.", "mrs.", "ms.", "dr.", "vs.", "st.", "no.", "inc.", "jr.", "sr."}
MAX_WORDS = 70


def ends_term(w):
    if w.lower().rstrip("\"')") in ABBREV:
        return False
    return bool(re.search(r"[.?!][\"')]*$", w))


def _no_comma(w):
    return re.sub(r"[,;:]+$", "", w)


QSTART = {"who", "what", "when", "where", "why", "how", "which", "do", "does", "did", "can", "could", "will", "would", "is", "are", "am", "should", "have", "has", "was", "were", "shall", "may"}


def _fix_q(out, term):
    """A '?' left on a deleted tag ('you know?') only stays if the kept sentence is a question."""
    if "?" not in term:
        return term
    first = None
    for w, _ in reversed(out):
        if first is not None and ends_term(w):
            break
        first = w
    # walk back to the start of the current sentence
    k = len(out) - 1
    while k > 0 and not ends_term(out[k - 1][0]):
        k -= 1
    first = out[k][0].lower().strip("\"'(,") if out else ""
    return term if first in QSTART else term.replace("?", ".")


# a comma after these is a filler artefact ("meet at, um, Friday"), never real punctuation
NO_COMMA_AFTER = {"a", "an", "the", "at", "on", "in", "to", "of", "for", "with", "from", "by", "and", "or", "that", "is", "are", "was", "were", "we", "i", "you", "he", "she", "they", "it", "my", "your", "our", "their", "this", "as", "if", "be", "have", "has", "will", "would", "can", "could", "should"}


NUMS = {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "noon", "midnight"}
CUES = {"actually", "wait", "sorry", "scratch", "mean", "rather", "correction"}


def render(words, labels):
    """Join the kept words (K and E) and repair spacing, punctuation and capitals."""
    out = []  # (text, original index)
    gap = False  # a deleted word sits between the last kept word and this one
    term = ""  # sentence-ending punctuation sitting on deleted words
    cue = False  # the deleted run held a self-correction cue, so the sentence goes on
    for i, (w, l) in enumerate(zip(words, labels)):
        if l == "D":
            gap = True
            if ends_term(w):
                term = re.search(r"[.?!][\"')]*$", w).group(0)
            if re.sub(r"\W", "", w).lower() in CUES:
                cue = True
            continue
        if cue and out and not ends_term(out[-1][0]):
            term = ""  # "on Tuesday. Actually, no, Wednesday" -> "on Wednesday"
        cue = False
        if term and out and not ends_term(out[-1][0]) and w[:1].isupper():
            out[-1] = (_no_comma(out[-1][0]) + _fix_q(out, term), out[-1][1])
        term = ""
        if gap and out and out[-1][0].endswith(",") and re.sub(r"\W", "", out[-1][0]).lower() in NO_COMMA_AFTER:
            out[-1] = (out[-1][0].rstrip(","), out[-1][1])
        gap = False
        out.append((w, i))
    if not out:
        return ""
    if term and not ends_term(out[-1][0]):
        out[-1] = (_no_comma(out[-1][0]) + _fix_q(out, term), out[-1][1])
    # capitals: first word if the original start was lost or already capitalised,
    # and a word that opens a sentence in the output and did so in the input
    res = []
    for k, (w, i) in enumerate(out):
        if w[:1].islower() or (w[:1] in "\"'(" and w[1:2].islower()):
            if k == 0:
                if words[0][:1].isupper() or labels[0] == "D":
                    w = _cap(w)
            elif ends_term(res[-1]) and (i == 0 or ends_term(words[i - 1]) or labels[i - 1] == "D"):
                w = _cap(w)
        res.append(w)
    text = " ".join(res)
    text = re.sub(r"\s+([,.;:?!])", r"\1", text)
    text = re.sub(r",{2,}", ",", text)
    text = re.sub(r",\s*([.?!])", r"\1", text)
    text = re.sub(r"([.?!]),+", r"\1", text)
    text = re.sub(r"^[,;:\s]+", "", text)
    text = re.sub(r"[,;:]+$", "", text)
    return text


def _cap(w):
    m = re.match(r"^([\"'(]*)(.)(.*)$", w, flags=re.S)
    return m.group(1) + m.group(2).upper() + m.group(3) if m else w


class Tagger:
    def __init__(self, model_dir, threads=2):
        model_dir = os.path.expanduser(model_dir)
        path = os.path.join(model_dir, "model.int8.onnx")
        if not os.path.exists(path):
            path = os.path.join(model_dir, "model.onnx")
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = 1
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.sess = ort.InferenceSession(path, so, providers=["CPUExecutionProvider"])
        self.input_names = [i.name for i in self.sess.get_inputs()]
        self.tok = Tokenizer.from_file(os.path.join(model_dir, "tokenizer.json"))
        self.tok.no_truncation()
        self.tok.no_padding()

    # ---- model call on one chunk of words
    def _tag_chunk(self, words):
        enc = self.tok.encode(words, is_pretokenized=True)
        ids = np.array([enc.ids], dtype=np.int64)
        feeds = {"input_ids": ids, "attention_mask": np.ones_like(ids)}
        if "token_type_ids" in self.input_names:
            feeds["token_type_ids"] = np.zeros_like(ids)
        logits = self.sess.run(None, feeds)[0][0]
        pred = logits.argmax(-1)
        labels = ["K"] * len(words)
        seen = set()
        for pos, wid in enumerate(enc.word_ids):
            if wid is None or wid in seen:
                continue
            seen.add(wid)
            labels[wid] = LABELS[int(pred[pos])]
        return labels

    def tag(self, text):
        words = text.split()
        if not words:
            return []
        labels = []
        for chunk in self._chunks(words):
            labels += self._tag_chunk(chunk)
        labels = self._rules(words, labels)
        return list(zip(words, labels))

    @staticmethod
    def _chunks(words):
        if len(words) <= MAX_WORDS:
            return [words]
        chunks, cur = [], []
        for w in words:
            cur.append(w)
            if (len(cur) >= 40 and ends_term(w)) or len(cur) >= MAX_WORDS:
                chunks.append(cur)
                cur = []
        if cur:
            chunks.append(cur)
        return chunks

    @staticmethod
    def _rules(words, labels):
        """Small fixes for things the model should never do."""
        labels = list(labels)
        # two-word fillers move together: a split tag ("I/D mean/K") is repaired
        for i in range(len(words) - 1):
            pair = (re.sub(r"\W", "", words[i]).lower(), re.sub(r"\W", "", words[i + 1]).lower())
            if pair in (("i", "mean"), ("you", "know")) and labels[i] != labels[i + 1] and "E" not in (labels[i], labels[i + 1]):
                set_off = bool(re.search(r"[,.?!]$", words[i + 1])) or (i > 0 and words[i - 1].endswith(","))
                labels[i] = labels[i + 1] = "D" if set_off else "K"
        # "I mean," opening a sentence is a filler, not part of the sentence
        for i in range(len(words) - 1):
            starts = i == 0 or ends_term(words[i - 1])
            if starts and words[i].lower() == "i" and words[i + 1].lower() == "mean," and "E" not in labels[i : i + 2]:
                labels[i] = labels[i + 1] = "D"
        # "actually, no" / "wait, no": the "no" is part of the cue, not an answer
        for i in range(len(words) - 1):
            a = re.sub(r"\W", "", words[i]).lower()
            b = re.sub(r"\W", "", words[i + 1]).lower()
            if a in ("actually", "wait", "oh") and b in ("no", "nope", "nah") and labels[i] == "D":
                labels[i + 1] = "D"
        # "on Tuesday. Actually, no, Wednesday": when a deleted cue sits between two words
        # of the same kind (names/days, numbers, pronouns), the first one is the mistake.
        PRON = {"he", "she", "him", "her", "they", "them", "his", "hers", "we", "us", "i", "me", "you", "it"}

        def kind(w):
            c = re.sub(r"[^\w']", "", w)
            if not c:
                return None
            if c.lower() in PRON:
                return "pron"
            if re.fullmatch(r"\d+(st|nd|rd|th)?", c.lower()) or c.lower() in NUMS:
                return "num"
            if c[0].isupper():
                return "cap"
            return None

        i = 0
        while i < len(words):
            if labels[i] == "D" and re.sub(r"\W", "", words[i]).lower() in CUES | {"no"}:
                j = i
                while j < len(words) and labels[j] == "D":
                    j += 1
                k = i - 1
                if 0 <= k and j < len(words) and labels[k] == "K" and labels[j] == "K":
                    a, b = kind(words[k]), kind(words[j])
                    first_of_sentence = k == 0 or ends_term(words[k - 1])
                    if a and a == b and not (a == "cap" and first_of_sentence):
                        labels[k] = "D"
                i = j
            i += 1
        # a lone word is never deleted: keep it so we never return an empty paste
        if len(words) == 1:
            labels[0] = "K" if labels[0] == "D" else labels[0]
        return labels

    def clean(self, text):
        tagged = self.tag(text)
        if not tagged:
            return text, False
        words = [w for w, _ in tagged]
        labels = [l for _, l in tagged]
        return render(words, labels), "E" in labels
