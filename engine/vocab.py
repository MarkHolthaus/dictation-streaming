"""Fix your own names and codes that speech-to-text splits or misspells.

Examples (made-up terms): "Acme2 Go" -> "Acme2Go", "ABCCD 101" -> "ABCD101", "WXYZ 311" -> "WXYZ311".
Add your own names and codes to CORE below.
Terms come from data/vocab.txt (built by the autocorrect miner) plus a core list.

Cleanup may never reword the speaker, so the joiner is narrow:
- It only joins split pieces into real terms: CORE, CamelCase, or a code with a digit inside
  letters ("crawl4ai", "k8s"). A plain word plus a number ("phase 2", "stage 1") is never joined.
- A one-letter near miss ("Acme2 Gp") only maps to a CORE or CamelCase term, only across two or
  more pieces, and never changes a digit.
- A single token is only touched for a CORE term ("acme2go" -> "Acme2Go"). "B-5" stays "B-5".
- A lowercase vocab.txt term never lowercases the input: the join keeps the speaker's own letters.
"""

from __future__ import annotations

import os
import re

CORE = [
    "ollama", "MLX", "Parakeet", "Claude", "Claude Code", "ChatGPT", "Gemini", "GitHub",
    # Add your own names and course or project codes here, written the way you want them typed.
    # A code is 2 to 5 capitals plus 3 digits ("ABCD101"). A name with a digit inside letters
    # ("Acme2Go") is joined from split speech ("Acme 2 Go").
]

MAX_TERM = 20
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_HEXPART = re.compile(r"[0-9a-f]*\d[0-9a-f]*", re.I)  # hex run that holds a digit


def _lev(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def keep_vocab_term(w: str) -> bool:
    """Filter for data/vocab.txt lines. Keeps only short name-like terms: drops UUIDs, hashes,
    phone numbers, pure numbers, versions, file names, domains and very long tokens."""
    if not w or len(w) > MAX_TERM:
        return False
    if not re.fullmatch(r"[A-Za-z0-9\-]+", w):  # ".", "/", "_", "@": files, domains, scripts
        return False
    if len(re.findall(r"[A-Za-z]", w)) < 2:  # pure numbers, phone numbers, "b5"-style stubs
        return False
    if _UUID.search(w):
        return False
    for part in w.split("-"):
        if len(part) >= 6 and _HEXPART.fullmatch(part) and re.search(r"[a-f]", part, re.I):
            return False  # git hash or id ("b63e6d5", "fed898bb4ca1")
    return True


def _is_camel(t: str) -> bool:
    return bool(re.search(r"[a-z][A-Z]", t))


def _inner_digit(t: str) -> bool:
    """Digit with letters on both sides: "acme2go", "crawl4ai", "k8s"."""
    return bool(re.search(r"[A-Za-z]\d+[A-Za-z]", t))


class VocabFixer:
    def __init__(self, vocab_path: str | None = None):
        terms = [t for t in CORE if " " not in t]
        if vocab_path and os.path.exists(vocab_path):
            with open(vocab_path, encoding="utf-8") as f:
                for line in f:
                    w = line.strip()
                    if keep_vocab_term(w) and (_is_camel(w) or _inner_digit(w)):
                        terms.append(w)
        self.terms = sorted(set(terms), key=len, reverse=True)
        # Cased targets keep their own form (CORE wins). Lowercase-only terms keep the speaker's letters.
        self.cased: dict[str, str] = {}
        self.lower: set[str] = set()
        for t in self.terms:
            k = self._sq(t)
            if t in CORE or not t.islower():
                old = self.cased.get(k)
                if old is None or t in CORE:
                    self.cased[k] = t
            else:
                self.lower.add(k)
        self.lower -= set(self.cased)
        # Near misses only against cased terms with a digit inside letters ("Acme2 Gp" -> Acme2Go).
        # Course codes are left to step 1, so "his 311" never becomes WXYZ311.
        self.near_keys = [k for k, t in self.cased.items() if _inner_digit(t)]
        # Course-code style: letters then digits.
        self.codes = [t for t in self.terms if re.fullmatch(r"[A-Z]{2,5}\d{3}", t)]

    @staticmethod
    def _sq(s: str) -> str:
        return re.sub(r"[\s\-]", "", s).lower()

    def fix(self, text: str) -> str:
        # 1. Course codes: "ABCCD 101", "WXYZ 311." Digits must match exactly. A one-letter miss
        #    only counts when the letters were written in capitals, so "his 311" stays.
        def code_sub(m: re.Match) -> str:
            raw_letters, digits = m.group(1), m.group(2)
            letters = raw_letters.upper()
            best, best_d = None, 9
            for c in self.codes:
                if c.endswith(digits) and _digits(c) == digits:
                    d = _lev(letters, c[: -len(digits)])
                    if d < best_d:
                        best, best_d = c, d
            if best and (best_d == 0 or (best_d == 1 and raw_letters.isupper() and len(raw_letters) >= 3)):
                return best
            return m.group(0)

        text = re.sub(r"\b([A-Za-z]{2,6})\s?(\d{3})\b", code_sub, text)

        # 2. Split terms: try joining 2 or 3 neighbouring tokens.
        tokens = re.split(r"(\s+)", text)
        words = [i for i, t in enumerate(tokens) if t and not t.isspace()]
        out = list(tokens)
        k = 0
        while k < len(words):
            done = False
            for n in (3, 2, 1):
                if k + n > len(words):
                    continue
                idx = words[k : k + n]
                raw = "".join(tokens[i] for i in idx)
                m = re.match(r"^(.*?)([.,!?;:]*)$", raw)
                core, punct = m.group(1), m.group(2)
                sq = self._sq(core)
                hit = None
                if n == 1:
                    # Single words: only CORE digit terms, so "zorp" never becomes "Zorp"
                    # and "B-5" never becomes "b5".
                    t = self.cased.get(sq)
                    if t in CORE and re.search(r"\d", sq):
                        hit = t
                else:
                    hit = self.cased.get(sq)
                    if hit is None and sq in self.lower:
                        hit = core  # the speaker's own letters and casing, spaces dropped
                    if hit is None and len(sq) >= 6 and re.search(r"\d", sq):
                        # One-letter miss on a cased digit term. Digits must be identical,
                        # so the edit can only touch letters.
                        dg = _digits(sq)
                        near = [self.cased[k2] for k2 in self.near_keys
                                if abs(len(k2) - len(sq)) <= 1 and _digits(k2) == dg and _lev(sq, k2) <= 1]
                        hit = near[0] if len(near) == 1 else None
                span = "".join(tokens[idx[0] : idx[-1] + 1])  # original text, spaces included
                if hit and hit + punct != span:
                    out[idx[0]] = hit + punct
                    for i in idx[1:]:
                        out[i] = ""
                    for i in range(idx[0] + 1, idx[-1]):
                        if tokens[i].isspace():
                            out[i] = ""
                    k += n
                    done = True
                    break
            if not done:
                k += 1
        # A join blanks the inner spaces itself, so the rest of the spacing is left exactly as is.
        return "".join(out)


if __name__ == "__main__":
    v = VocabFixer()
    for s in ["the Claude Code docs", "push it to Git Hub", "phase 2 is done"]:
        print(s, "->", v.fix(s))
