"""Typing autocorrect for the dictation app.

Conservative by design: a wrong fix is worse than a missed one.

fix(word, before) order:
  1. personal typo map (instant)
  2. skip: all caps, CamelCase, digits or code-like, under 2 letters,
     personal vocabulary, general dictionary word
  3. symspell (top 30k words of the bundled frequency list) edit distance 1 (distance 2 only for words of 6+ letters),
     best by frequency, with the previous word as a weak bigram tie-breaker
  4. context check (layer 3), only if 1-3 found nothing and the word is in a
     confusion set (engine/confusion_sets.txt: then/than, their/there/they're, ...).
     A masked LM (.models/context, int8 ONNX) scores each word of the set against
     the left context. The word is swapped only if another word of the set beats
     it by CONTEXT_MARGIN nats. Optional: without .models/context this layer is off.
     The model loads in a background thread, so the first calls skip this layer.
  5. otherwise None
"""
import bisect
import json
import math
import os
import re
import threading
from array import array

from symspellpy import SymSpell, Verbosity

_PREV_WORD = re.compile(r"[A-Za-z]+")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTEXT_DIR = os.path.join(ROOT, ".models", "context")
CONFUSION_PATH = os.path.join(ROOT, "engine", "confusion_sets.txt")
# Log-prob lead (nats) another word of the set needs over the typed word. Set on the
# tuning half of the author's own messages so unmodified text gets ~no fixes. The eval in
# engine/test_context.py reproduces this on your own messages (data/raw/user_messages.jsonl).
CONTEXT_MARGIN = 6.0


def load_confusion_sets(path):
    """word -> sorted list of every word it shares a set with (itself included)."""
    sets = {}
    if not os.path.exists(path):
        return sets
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip() or line.startswith("#"):
                continue
            words = line.lower().split()
            for w in words:
                sets.setdefault(w, set()).update(words)
    return {w: sorted(s) for w, s in sets.items()}


def pick_context(scores, typed, margin):
    """scores: lower-case set word -> log score. The winner, or None to keep `typed`."""
    best = max(scores, key=scores.get)
    if scores[typed] == float("-inf"):  # typed word outside the model's vocabulary subset
        return None
    if best == typed or scores[best] - scores[typed] < margin:
        return None
    return best


def _case(word, fix):
    """Capitalized in, capitalized out. Other casing stays as stored."""
    if len(word) > 1 and word[0].isupper() and word[1:].islower():
        return fix[0].upper() + fix[1:]
    if len(word) == 1 and word.isupper():
        return fix[0].upper() + fix[1:]
    return fix


class Autocorrect:
    def __init__(self, typo_map_path, vocab_path, context_dir=CONTEXT_DIR, context_wait=False,
                 context_margin=CONTEXT_MARGIN):
        """context_dir=None turns layer 3 off. context_wait=True loads it before returning."""
        data_dir = os.path.dirname(os.path.abspath(vocab_path))
        self._confusion = load_confusion_sets(CONFUSION_PATH)
        self.context_margin = context_margin
        self.context = None
        self.context_error = None
        if context_dir and self._confusion and os.path.exists(
                os.path.join(context_dir, "model.int8.onnx")):
            th = threading.Thread(target=self._load_context, args=(context_dir,), daemon=True)
            th.start()
            if context_wait:
                th.join()
        with open(typo_map_path, encoding="utf-8") as f:
            self._map = {k.lower(): v for k, v in json.load(f).items()}
        self._vocab = set()
        with open(vocab_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip().lower()
                if line:
                    self._vocab.add(line)
        # About 270k words, kept as sorted 64-bit hashes in an array (about 2 MB).
        # Hashes are only compared inside this process, so the hash seed does not matter.
        hashes = array("q")
        with open(os.path.join(data_dir, "dictionary.txt"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    hashes.append(hash(line))
        self._dict = array("q", sorted(hashes))
        del hashes
        # Words the user writes on purpose (2+ times in their messages or notes). Never guessed on.
        seen_path = os.path.join(data_dir, "seen.txt")
        self._seen = set()
        if os.path.exists(seen_path):
            with open(seen_path, encoding="utf-8") as f:
                self._seen = {w.strip() for w in f if w.strip()}
        # Words the user undid a fix on (backspace right after a swap). Never touched again.
        never = os.path.join(data_dir, "never_fix.txt")
        self._never = set()
        if os.path.exists(never):
            with open(never, encoding="utf-8") as f:
                self._never = {w.strip().lower() for w in f if w.strip()}
        # Word frequency (wordfreq zipf) for about 200k words, as parallel arrays.
        # A guess is only made when the suggestion is far more common than the typed word.
        self._zh, self._zv = array("q"), array("f")
        kw_path = os.path.join(data_dir, "known_words.txt")
        if os.path.exists(kw_path):
            pairs = []
            with open(kw_path, encoding="utf-8") as f:
                for line in f:
                    w, _, z = line.rstrip("\n").partition("\t")
                    if w:
                        pairs.append((hash(w), float(z)))
            pairs.sort()
            self._zh = array("q", (h for h, _ in pairs))
            self._zv = array("f", (z for _, z in pairs))
            del pairs
        bigram_path = os.path.join(data_dir, "bigrams.json")
        self._bigrams = {}
        if os.path.exists(bigram_path):
            with open(bigram_path, encoding="utf-8") as f:
                self._bigrams = json.load(f)
        self._ss = SymSpell(max_dictionary_edit_distance=2, prefix_length=7)
        self._ss.load_dictionary(os.path.join(data_dir, "symspell_freq.txt"),
                                 term_index=0, count_index=1)

    def _load_context(self, context_dir):
        try:
            try:
                from engine.context import ContextModel
            except ImportError:
                from context import ContextModel
            self.context = ContextModel(context_dir)
        except Exception as e:  # noqa: BLE001  layer 3 is optional
            self.context_error = e

    def context_scores(self, word, before=""):
        """{set word (lower case): log score} for a confusion-set word, else None."""
        ctx = self.context
        if ctx is None or len(word) < 2:
            return None
        norm = word.replace("’", "'")
        low = norm.lower()
        cands = self._confusion.get(low)
        if not cands or low in self._never:
            return None
        if any(c.isupper() for c in norm[1:]):  # ALL CAPS, CamelCase: leave alone
            return None
        # No left context (start of the app's buffer, which resets on Return, arrows and
        # clicks): the model would judge by word prior alone ("Knew" -> "New").
        if not any(ch.isalpha() for ch in before):
            return None
        cased = {c: _case(norm, c) for c in cands}
        scores = ctx.scores(before, list(cased.values()))
        return {c: scores[cased[c]] for c in cands}

    def context_fix(self, word, before=""):
        """Layer 3 alone: a better word from the word's confusion set, or None."""
        scores = self.context_scores(word, before)
        if scores is None:
            return None
        norm = word.replace("’", "'")
        best = pick_context(scores, norm.lower(), self.context_margin)
        if best is None:
            return None
        out = _case(norm, best)
        if "’" in word:
            out = out.replace("'", "’")
        return out

    def _zipf(self, word):
        h = hash(word)
        i = bisect.bisect_left(self._zh, h)
        return self._zv[i] if i < len(self._zh) and self._zh[i] == h else 0.0

    def _in_dict(self, word):
        h = hash(word)
        i = bisect.bisect_left(self._dict, h)
        return i < len(self._dict) and self._dict[i] == h

    def fix(self, word, before=""):
        """Return the corrected word, or None to leave it alone."""
        if not word or word.lower() in self._never:
            return None
        out = self._spell(word, before)
        if out is None and self.context is not None:
            out = self.context_fix(word, before)
        return out

    def _spell(self, word, before=""):
        """Layers 1-3: typo map, skips, symspell."""
        if not word or len(word) < 2 or not word.isalpha():
            return None
        low = word.lower()
        if low in getattr(self, "_never", ()):
            return None
        if not word.isupper():
            fixed = self._map.get(low)
            if fixed is not None:
                return _case(word, fixed)
        if word.isupper() or any(c.isupper() for c in word[1:]):
            return None
        if low in self._vocab or self._in_dict(low):
            return None
        if len(low) < 3:  # px, vh, fg: abbreviations, not typos
            return None
        if low in self._seen:  # stderr, ad hoc: the user's own words
            return None
        maxd = 2 if len(low) >= 6 else 1
        sugg = [s for s in self._ss.lookup(low, Verbosity.ALL, max_edit_distance=maxd)
                if s.term != low and s.distance <= maxd]
        if not sugg:
            return None
        best = min(s.distance for s in sugg)
        pool = [s for s in sugg if s.distance == best]
        prev = None
        if before:
            words = _PREV_WORD.findall(before[-200:])
            if words:
                prev = words[-1].lower()

        def score(s):
            freq = math.log10(s.count + 1)
            if prev is None:
                return freq
            return freq + 0.5 * math.log10(1 + self._bigrams.get(prev + " " + s.term, 0))

        top = max(pool, key=score)
        # Guards, because a wrong fix is worse than a missed one.
        if top.term[0] != low[0]:  # typos almost always keep the first letter
            return None
        if top.distance == 2 and abs(len(top.term) - len(low)) > 1:  # hardrive -> arrive
            return None
        zw = self._zipf(low)
        if top.distance == 2 and zw == 0.0:  # offsuit, docstrings: jargon English has never seen
            return None
        if zw >= 3.0 or self._zipf(top.term) - zw < 1.8:  # strobing vs stroking: both real
            return None
        return _case(word, top.term)
