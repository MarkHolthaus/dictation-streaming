"""Fix-up editor for back-reference corrections ("make that Wednesday").

Small model (Qwen3 0.6B, 4-bit, about 0.4 GB). Loaded on first use, unloaded after
2 minutes idle. Only runs on chunks the tagger flagged needs_edit, so most
dictations never load it.
"""

from __future__ import annotations

import os
import threading
import time

MODEL = os.environ.get("DICTATION_EDITOR", "mlx-community/Qwen3-0.6B-4bit")
IDLE_S = 120

SYSTEM = (
    "You fix dictated text where the speaker corrected themselves by pointing back. "
    "Apply the correction to the earlier words, then remove the correction phrase. "
    "Change nothing else. Keep every other word, the order and the punctuation. "
    "Never add words, never summarize, never answer. Output only the fixed text."
)

EXAMPLES = [
    ("Meet at 3 on Tuesday. Actually make that Wednesday.", "Meet at 3 on Wednesday."),
    ("Order the box, make that boxes, from Amazon.", "Order the boxes from Amazon."),
    ("Pat's car, his truck I mean, is outside.", "Pat's truck is outside."),
    ("Buy milk, eggs and bread. Change the second one to butter.", "Buy milk, butter and bread."),
    ("Send it to the team on Friday, no, change that to Monday morning.", "Send it to the team on Monday morning."),
]


class Editor:
    def __init__(self, model_id: str = MODEL):
        self.model_id = model_id
        self.model = None
        self.tok = None
        self.last_used = 0.0
        self.lock = threading.Lock()
        self.resident = os.environ.get("DICTATION_EDITOR_RESIDENT") == "1"
        threading.Thread(target=self._reaper, daemon=True).start()
        # Importing mlx_lm takes about 1.4 s, more than loading the 0.6B weights (0.7 s).
        # Do it now in the background so an on-demand load costs about 1.3 s, not 2.8 s.
        threading.Thread(target=self._startup, daemon=True).start()

    def _startup(self):
        import mlx_lm  # noqa: F401
        from mlx_lm.sample_utils import make_sampler  # noqa: F401

        if self.resident:
            self.preload()

    def _load(self):
        from mlx_lm import load

        self.model, self.tok = load(self.model_id)

    def _reaper(self):
        while True:
            time.sleep(15)
            with self.lock:
                if self.model is not None and not self.resident and time.time() - self.last_used > IDLE_S:
                    import mlx.core as mx

                    self.model, self.tok = None, None
                    mx.clear_cache()

    def preload(self) -> None:
        """Start loading in the background (called mid-dictation when a back-reference shows up)."""

        def run():
            with self.lock:
                if self.model is None:
                    self._load()
                    self._warm()
                self.last_used = time.time()

        threading.Thread(target=run, daemon=True).start()

    def _warm(self):
        from mlx_lm import generate

        generate(self.model, self.tok, prompt="hi", max_tokens=1, verbose=False)

    def loaded(self) -> bool:
        return self.model is not None

    def fix(self, text: str) -> str:
        from mlx_lm import generate
        from mlx_lm.sample_utils import make_sampler

        with self.lock:
            if self.model is None:
                self._load()
            self.last_used = time.time()
            msgs = [{"role": "system", "content": SYSTEM}]
            for a, b in EXAMPLES:
                msgs += [{"role": "user", "content": a}, {"role": "assistant", "content": b}]
            msgs.append({"role": "user", "content": text})
            prompt = self.tok.apply_chat_template(
                msgs, add_generation_prompt=True, tokenize=False, enable_thinking=False
            )
            out = generate(
                self.model,
                self.tok,
                prompt=prompt,
                max_tokens=int(len(text.split()) * 2 + 20),
                sampler=make_sampler(temp=0.0),
                verbose=False,
            )
        out = out.split("</think>")[-1].strip().strip('"')
        return out if _safe(text, out) else text


def _safe(src: str, out: str) -> bool:
    """Reject outputs that rewrote too much. The editor may only swap a few words."""
    if not out:
        return False
    a, b = src.lower().split(), out.lower().split()
    if len(b) > len(a) or len(b) < len(a) * 0.3:
        return False
    new = [w for w in b if w not in a]
    return len(new) <= 3


if __name__ == "__main__":
    e = Editor()
    tests = [
        "We should meet at 3 on Tuesday. Actually make that Wednesday.",
        "Put the boxes in the garage, make that the basement.",
        "Call John at noon, no, change that to two.",
    ]
    for t in tests:
        s = time.perf_counter()
        r = e.fix(t)
        print(f"{(time.perf_counter()-s)*1000:.0f} ms | {t} -> {r}")
