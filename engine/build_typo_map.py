"""Mine your own typing for typos and build the autocorrect data files.

Input: data/raw/user_messages.jsonl, one {"text": "..."} per line (your own messages). Optional:
DICTATION_NOTES_DIR, a folder of markdown notes that adds your own terms to the vocabulary.
For a first run without any personal text, use engine/setup_data.py instead.

Writes (under data/):
  typo_map.json   {typo: fix}, sorted by how often you typed the typo
  typo_report.md  top 100 typos with counts and example contexts
  vocab.txt       personal vocabulary (lowercase, one per line)
  dictionary.txt  general dictionary used to decide "already a word"
  bigrams.json    word-pair counts from your messages (count >= 2)

Run with the project venv:  .venv/bin/python engine/build_typo_map.py
"""
import collections
import glob
import json
import math
import os
import re
import sys

from symspellpy import SymSpell, Verbosity
import symspellpy
from wordfreq import top_n_list, zipf_frequency

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
RAW = os.path.join(DATA, "raw", "user_messages.jsonl")
NOTES = os.path.expanduser(os.environ.get("DICTATION_NOTES_DIR", ""))  # optional markdown notes folder
SYS_DICT = "/usr/share/dict/words"

ALWAYS_VOCAB = ["ollama", "MLX", "Parakeet", "GitHub", "ChatGPT"]

# Contractions typed without the apostrophe. Applied only when seen 2+ times.
CONTRACTIONS = {
    "dont": "don't", "doesnt": "doesn't", "didnt": "didn't", "isnt": "isn't",
    "arent": "aren't", "wasnt": "wasn't", "werent": "weren't",
    "shouldnt": "shouldn't", "wouldnt": "wouldn't", "couldnt": "couldn't",
    "hasnt": "hasn't", "havent": "haven't", "thats": "that's",
    "whats": "what's", "youre": "you're", "theyre": "they're",
    "im": "I'm", "ive": "I've",
}

# Reviewed by hand after a run: a fix on this list is dropped. Empty here, add your own.
DENY = set()

# Typos where the symspell pick was wrong and the right fix is clear from context. Add your own.
OVERRIDE = {}

# Terms that are typed on purpose (tools, abbreviations). Treated as vocabulary, so they are
# never "fixed" by the map or by symspell. A starter list of common developer words.
KEEP = {
    "stderr", "stdlib", "stddev", "openssl", "numpy", "pyarrow", "zsh", "npm", "npx", "rclone",
    "cmake", "plist", "launchd", "subprocess", "uvicorn", "crontab", "heredoc", "sshd", "mtime",
    "docstring", "namespace", "namespaces", "filesystem", "allowlist", "hardcoded", "hardcode",
    "codebase", "autocomplete", "monkeypatch", "tooltip", "stylesheet", "stylesheets", "dropdown",
    "paywall", "lifecycle", "gcloud", "webm", "tsx", "deps", "repl", "configs", "evals",
    "refactors", "rebased", "deduped", "memoized", "vendored", "inode", "backticks", "validator",
    "loopback", "passthrough", "typecheck", "allowlist", "textbox", "tracebacks", "substrings",
}

FENCE = re.compile(r"```.*?```", re.S)
INLINE = re.compile(r"`[^`]*`")
URL = re.compile(r"\S+://\S+|www\.\S+")
PATHLIKE = re.compile(r"\S*[/\\~@]\S*")
TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9']*")
VAULT_TOKEN = re.compile(
    r"\b[A-Z][A-Za-z0-9]*\b"                 # Capitalized, ALLCAPS, MyTerm2
    r"|\b[a-z]+[A-Z][A-Za-z0-9]*\b"          # camelCase
    r"|\b[A-Za-z0-9]+(?:[_.\-][A-Za-z0-9]+)+\b"  # code-like: codex-bridge, a_b
)
CODE_SPAN = re.compile(r"```.*?```|`[^`\n]+`", re.S)
VAULT_LOWER = re.compile(r"\b[a-z][a-z0-9]+\b")
# Words that are typos your own notes contain, even inside code spans.
VOCAB_EXCLUDE = {"awya"}
SKIP_PARTS = {"_to_delete"}


def clean(text):
    t = FENCE.sub(" ", text)
    t = INLINE.sub(" ", t)
    t = URL.sub(" ", t)
    t = PATHLIKE.sub(" ", t)
    return t


def load_messages(path=RAW):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            out.append(json.loads(line)["text"])
    return out


SYMSPELL_TOP_N = 30000  # memory: the full 82k list needs ~150 MB of index


def write_symspell_list(path=os.path.join(DATA, "symspell_freq.txt")):
    """The bundled frequency list, top N words by count (it is sorted by count)."""
    src = os.path.join(os.path.dirname(symspellpy.__file__),
                       "frequency_dictionary_en_82_765.txt")
    with open(src, encoding="utf-8") as f:
        rows = [line for line in f if line.strip()][:SYMSPELL_TOP_N]
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(rows)
    return path


def load_symspell():
    path = write_symspell_list()
    ss = SymSpell(max_dictionary_edit_distance=2, prefix_length=7)
    ss.load_dictionary(path, term_index=0, count_index=1)
    return ss


def build_dictionary(ss):
    """General dictionary: system word list, the symspell list, and common
    wordfreq words. wordfreq lists misspellings like teh (3.04) and inthe
    (2.58), so its cut is 3.5, not 1.5."""
    words = set()
    src = os.path.join(os.path.dirname(symspellpy.__file__),
                       "frequency_dictionary_en_82_765.txt")
    with open(src, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                words.add(line.split()[0])
    if os.path.exists(SYS_DICT):  # macOS ships /usr/share/dict/words
        with open(SYS_DICT, encoding="utf-8", errors="ignore") as f:
            for line in f:
                w = line.strip().lower()
                if w.isalpha():
                    words.add(w)
    for w in top_n_list("en", 200000):
        if w.isalpha() and zipf_frequency(w, "en") >= 3.5:
            words.add(w)
    return words


def build_vocab():
    counts = collections.Counter()
    code_counts = collections.Counter()
    for md in (glob.glob(os.path.join(NOTES, "**", "*.md"), recursive=True) if NOTES else []):
        rel = os.path.relpath(md, NOTES).split(os.sep)
        if any(p in SKIP_PARTS or p.startswith(".") for p in rel[:-1]):
            continue
        if any(p in SKIP_PARTS for p in rel):
            continue
        try:
            with open(md, encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError:
            continue
        for m in VAULT_TOKEN.finditer(text):
            counts[m.group(0)] += 1
        # Lowercase identifiers in code spans (npm, zsh, mtime) are terms, not typos.
        for span in CODE_SPAN.finditer(text):
            for m in VAULT_LOWER.finditer(span.group(0)):
                code_counts[m.group(0)] += 1
    vocab = set(w.lower() for w in ALWAYS_VOCAB) | set(KEEP)
    for tok, n in code_counts.items():
        if n >= 2 and tok not in VOCAB_EXCLUDE:
            vocab.add(tok)
    for tok, n in counts.items():
        if n < 3:
            continue
        vocab.add(tok.lower())
        if re.search(r"[_.\-]", tok):
            for part in re.split(r"[_.\-]", tok):
                if len(part) >= 2 and part.isalpha():
                    vocab.add(part.lower())
    return vocab


def tokens_of(text):
    """Yield (token, preceding_text) for each word in a message."""
    c = clean(text)
    for m in TOKEN.finditer(c):
        yield m.group(0), c[:m.start()]


def candidate_counts(messages, dictionary, vocab):
    counts = collections.Counter()
    for text in messages:
        for tok, _ in tokens_of(text):
            if "'" in tok or len(tok) < 2 or tok.isupper():
                continue
            if any(ch.isdigit() for ch in tok):
                continue
            if any(ch.isupper() for ch in tok[1:]):
                continue
            low = tok.lower()
            if low in dictionary or low in vocab:
                continue
            counts[low] += 1
    return counts


def propose(word, ss, dictionary):
    """Return (fix, distance) or (None, None). Merged words get distance 0.
    Order: one-edit spelling fix, then merge, then two-edit fix (6+ letters)."""
    sugg = [s for s in ss.lookup(word, Verbosity.ALL, max_edit_distance=2)
            if s.term != word]
    one = sorted((s for s in sugg if s.distance == 1), key=lambda s: -s.count)
    if one:
        return one[0].term, 1
    if len(word) >= 5:
        seg = ss.word_segmentation(word, max_edit_distance=0)
        parts = seg.corrected_string.split(" ")
        if len(parts) >= 2 and all(len(p) >= 2 and zipf_frequency(p, "en") >= 4.0
                                   and p in dictionary for p in parts):
            return seg.corrected_string, 0
    two = sorted((s for s in sugg if s.distance == 2), key=lambda s: -s.count)
    if two and len(word) >= 6:
        return two[0].term, 2
    return None, None


def keep(word, count, fix, dist):
    # Fix must be a common word. Short tokens (fn, qd, npc) are usually
    # abbreviations, so they need a very common target.
    fz = zipf_frequency(fix, "en")
    if fz < 3.0 or (len(word) < 4 and fz < 5.0):
        return False
    if dist == 0:  # merged words
        return count >= 2
    if dist == 1:
        return count >= 2 or (count == 1 and fz >= 4.5)
    return count >= 2 and len(fix) >= 6  # dist 2 only for longer words


def build_map(messages, ss, dictionary, vocab):
    counts = candidate_counts(messages, dictionary, vocab)
    result = {}
    for word, n in counts.items():
        if word in DENY:
            continue
        if word in OVERRIDE:
            result[word] = (OVERRIDE[word], n)
            continue
        fix, dist = propose(word, ss, dictionary)
        if fix is None or not keep(word, n, fix, dist):
            continue
        result[word] = (fix, n)
    for word in OVERRIDE:
        n = sum(1 for t in messages for tok, _ in tokens_of(t) if tok.lower() == word)
        if n >= 2 and word not in DENY:
            result[word] = (OVERRIDE[word], n)
    for word, fix in CONTRACTIONS.items():
        n = sum(1 for t in messages for tok, _ in tokens_of(t) if tok.lower() == word)
        if n >= 2 and word not in DENY:
            result[word] = (fix, n)
    return result


def build_bigrams(messages, min_count=2):
    counts = collections.Counter()
    for text in messages:
        prev = None
        for tok, _ in tokens_of(text):
            low = tok.lower()
            if prev is not None:
                counts[prev + " " + low] += 1
            prev = low
    return {k: v for k, v in counts.items() if v >= min_count}


def write_report(result, messages, path):
    top = sorted(result.items(), key=lambda kv: -kv[1][1])[:100]
    lines = ["# Typo report", "",
             "Top 100 typos from your typed messages. Count is the number of times the typo appears. "
             "Some messages are dictated, so a few typos are speech errors.", ""]
    for i, (typo, (fix, n)) in enumerate(top, 1):
        lines.append(f"{i}. `{typo}` -> `{fix}` ({n})")
        found = 0
        pat = re.compile(r"\b" + re.escape(typo) + r"\b", re.I)
        for text in messages:
            c = clean(text)
            m = pat.search(c)
            if not m:
                continue
            s = max(0, m.start() - 50)
            e = min(len(c), m.end() + 30)
            snippet = " ".join(c[s:e].split())
            lines.append(f"   - \"...{snippet}...\"")
            found += 1
            if found == 3:
                break
        lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def write_known_words(path=os.path.join(DATA, "known_words.txt"), top_n=300_000):
    """word<TAB>zipf frequency for the wordfreq English list (public data). autocorrect.py only
    guesses when the suggestion is far more common than the typed word."""
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        for w in top_n_list("en", top_n):
            if w.isalpha():
                f.write(f"{w}\t{zipf_frequency(w, 'en'):.2f}\n")
                n += 1
    return n


def main():
    messages = load_messages()
    print("messages", len(messages))
    ss = load_symspell()
    vocab = build_vocab()
    dictionary = build_dictionary(ss)
    result = build_map(messages, ss, dictionary, vocab)
    ordered = sorted(result.items(), key=lambda kv: -kv[1][1])
    typo_map = {k: v[0] for k, v in ordered}
    with open(os.path.join(DATA, "typo_map.json"), "w", encoding="utf-8") as f:
        json.dump(typo_map, f, ensure_ascii=False, indent=1)
    with open(os.path.join(DATA, "vocab.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(vocab)) + "\n")
    with open(os.path.join(DATA, "dictionary.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(dictionary)) + "\n")
    with open(os.path.join(DATA, "bigrams.json"), "w", encoding="utf-8") as f:
        json.dump(build_bigrams(messages), f, ensure_ascii=False)
    write_report(result, messages, os.path.join(DATA, "typo_report.md"))
    print("vocab", len(vocab), "dictionary", len(dictionary), "typo_map", len(typo_map))


if __name__ == "__main__":
    sys.exit(main())
