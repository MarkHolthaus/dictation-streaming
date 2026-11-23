"""Build data/ from public word lists. No personal text needed.

    .venv/bin/python engine/setup_data.py

Writes into data/ (gitignored):
  symspell_freq.txt  top 30k words of the frequency list bundled with symspellpy
  dictionary.txt     symspell list + /usr/share/dict/words (macOS) + common wordfreq words
  known_words.txt    word<TAB>zipf frequency, from wordfreq
  typo_map.json      a small starter map: contractions typed without the apostrophe and a few
                     very common typos. Replace it with your own by running build_typo_map.py
                     on your messages (data/raw/user_messages.jsonl).
  vocab.txt          the starter vocabulary (ALWAYS_VOCAB and KEEP in build_typo_map.py)

Needs network only for the wordfreq and symspellpy packages (already in requirements.txt).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_typo_map as B  # noqa: E402

STARTER_TYPOS = {
    "teh": "the", "adn": "and", "taht": "that", "recieve": "receive", "seperate": "separate",
    "definately": "definitely", "occured": "occurred", "untill": "until", "wich": "which",
    "becuase": "because", "awya": "away", "anthing": "anything", "wierd": "weird",
    "thier": "their", "alot": "a lot", "tommorow": "tomorrow", "accross": "across",
}


def main():
    os.makedirs(B.DATA, exist_ok=True)
    B.write_symspell_list()
    ss = B.load_symspell()
    dictionary = B.build_dictionary(ss)
    with open(os.path.join(B.DATA, "dictionary.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(dictionary)) + "\n")
    n_known = B.write_known_words()
    typo_map = dict(B.CONTRACTIONS)
    typo_map.update(STARTER_TYPOS)
    with open(os.path.join(B.DATA, "typo_map.json"), "w", encoding="utf-8") as f:
        json.dump(typo_map, f, ensure_ascii=False, indent=1)
    vocab = {w.lower() for w in B.ALWAYS_VOCAB} | set(B.KEEP)
    with open(os.path.join(B.DATA, "vocab.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(vocab)) + "\n")
    print(f"dictionary {len(dictionary)}, known_words {n_known}, typo_map {len(typo_map)}, vocab {len(vocab)}")
    print(f"written to {B.DATA}")


if __name__ == "__main__":
    sys.exit(main())
