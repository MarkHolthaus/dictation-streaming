"""Speech to text on the Mac with Parakeet (MLX).

Audio comes in as 16 kHz mono float32. The session cuts it into segments at
pauses. A segment is transcribed as soon as it closes, while the user keeps
talking, so at stop only the last segment is left to do.
"""

from __future__ import annotations

import os
import threading
import time

import numpy as np

SR = 16000
DEFAULT_MODEL = os.environ.get("DICTATION_ASR", "mlx-community/parakeet-tdt-0.6b-v2")


class ASR:
    def __init__(self, model_id: str = DEFAULT_MODEL, bits: int | None = int(os.environ.get("DICTATION_ASR_BITS", "8"))):
        import mlx.core as mx
        import mlx.nn as nn
        from parakeet_mlx import from_pretrained
        from parakeet_mlx.audio import get_logmel

        self.mx = mx
        # MLX keeps freed GPU buffers in a cache by default (it held about 400 MB idle).
        # Cap it so idle memory stays near the size of the weights.
        mx.set_cache_limit(64 * 1024 * 1024)
        self._get_logmel = get_logmel
        self.model_id = model_id
        self.model = from_pretrained(model_id, dtype=mx.bfloat16)
        self.bits = bits
        if bits:
            # Quantize big linear layers to cut memory roughly in half.
            nn.quantize(
                self.model,
                group_size=64,
                bits=bits,
                class_predicate=lambda p, m: isinstance(m, nn.Linear)
                and m.weight.shape[-1] % 64 == 0,
            )
        mx.eval(self.model.parameters())
        mx.clear_cache()  # drop the bf16 originals freed by quantize
        self.lock = threading.Lock()
        # Warm up so the first real call is fast.
        self.transcribe(np.zeros(SR // 2, dtype=np.float32))

    def transcribe(self, audio: np.ndarray) -> str:
        if len(audio) < SR // 10:
            return ""
        mx = self.mx
        with self.lock:
            mel = self._get_logmel(
                mx.array(audio, dtype=mx.float32), self.model.preprocessor_config
            )
            result = self.model.generate(mel.astype(mx.bfloat16))[0]
        return result.text.strip()


class Segmenter:
    """Cuts a live audio stream at pauses using a simple energy gate."""

    def __init__(self, pause_s: float = 0.55, max_s: float = 20.0, frame_ms: int = 20):
        self.frame = SR * frame_ms // 1000
        self.pause_frames = int(pause_s * 1000 / frame_ms)
        self.max_len = int(max_s * SR)
        self.buf = np.zeros(0, dtype=np.float32)
        self.noise = 0.003  # running noise floor estimate (RMS)
        self.silent_run = 0
        self.had_speech = False
        self._scan = 0  # samples already scanned for energy

    def push(self, audio: np.ndarray) -> list[np.ndarray]:
        """Add audio, return any segments that just closed."""
        self.buf = np.concatenate([self.buf, audio])
        closed = []
        while self._scan + self.frame <= len(self.buf):
            f = self.buf[self._scan : self._scan + self.frame]
            rms = float(np.sqrt(np.mean(f * f)) + 1e-9)
            speech = rms > max(0.008, self.noise * 3.0)
            if not speech:
                self.noise = 0.95 * self.noise + 0.05 * rms
                self.silent_run += 1
            else:
                self.silent_run = 0
                self.had_speech = True
            self._scan += self.frame
            cut_pause = self.had_speech and self.silent_run >= self.pause_frames
            cut_long = self._scan >= self.max_len
            if cut_pause or cut_long:
                cut = self._scan
                if cut_long and not cut_pause:
                    cut = self._quietest_cut()
                seg, self.buf = self.buf[:cut], self.buf[cut:]
                self._scan = max(0, self._scan - cut)
                if self.had_speech:
                    closed.append(seg)
                self.silent_run = 0
                self.had_speech = False
        return closed

    def _quietest_cut(self) -> int:
        """For an overlong segment, cut at the quietest 100 ms in the last 5 s."""
        win = SR // 10
        start = max(0, self._scan - 5 * SR)
        best, best_e = self._scan, 1e9
        for i in range(start, self._scan - win, win // 2):
            e = float(np.mean(self.buf[i : i + win] ** 2))
            if e < best_e:
                best, best_e = i + win // 2, e
        return best

    def flush(self) -> np.ndarray:
        seg, self.buf = self.buf, np.zeros(0, dtype=np.float32)
        self._scan = 0
        return seg


class DictationSession:
    """One hotkey hold. Transcribes closed segments in a background thread."""

    def __init__(self, asr: ASR, on_partial=None):
        self.asr = asr
        self.seg = Segmenter()
        self.texts: list[str] = []
        self.pending: list[threading.Thread] = []
        self.on_partial = on_partial
        self._lock = threading.Lock()
        self._slots: list[str | None] = []
        self._peek_text = ""
        self._peek_busy = False
        self._last_peek = time.time()
        self._closed = False

    def add_audio(self, audio: np.ndarray) -> None:
        for s in self.seg.push(audio):
            self._start(s)
        # Live peek at the open segment about once a second, for the overlay
        # and so a back-reference can be spotted before the user lets go.
        now = time.time()
        if (self.on_partial and not self._peek_busy and now - self._last_peek > 1.0
                and len(self.seg.buf) > SR):
            self._last_peek = now
            self._peek_busy = True
            buf = self.seg.buf.copy()

            def peek():
                try:
                    if not self._closed:
                        self._peek_text = self.asr.transcribe(buf)
                        self.on_partial(self.partial_text())
                finally:
                    self._peek_busy = False

            threading.Thread(target=peek, daemon=True).start()

    def _start(self, audio: np.ndarray) -> None:
        idx = len(self._slots)
        self._slots.append(None)

        def run():
            text = self.asr.transcribe(audio)
            with self._lock:
                self._slots[idx] = text
            self._peek_text = ""
            if self.on_partial and not self._closed:
                self.on_partial(self.partial_text())

        t = threading.Thread(target=run, daemon=True)
        t.start()
        self.pending.append(t)

    def partial_text(self) -> str:
        with self._lock:
            parts = [t for t in self._slots if t]
        if self._peek_text and not self._closed:
            parts.append(self._peek_text)
        return " ".join(parts)

    def finish(self) -> list[str]:
        """Transcribe the tail, wait for all segments, return segment texts in order."""
        self._closed = True
        self._peek_text = ""
        tail = self.seg.flush()
        # 4 loud frames (80 ms) keeps a short last word ("yes", "it"). A 20 ms click
        # still fails, so trailing noise does not become "Yeah.". Meetings keep the default 8.
        if len(tail) > SR // 10 and has_speech(tail, self.seg.noise, min_frames=4):
            self._start(tail)
        for t in self.pending:
            t.join()
        self.asr.mx.clear_cache()
        with self._lock:
            return [t for t in self._slots if t]


def has_speech(audio: np.ndarray, noise: float, frame: int = SR // 50, min_frames: int = 8) -> bool:
    """True if at least min_frames 20 ms frames are above the speech gate.
    Stops Parakeet from inventing words ("Yeah.") on trailing silence."""
    n = len(audio) // frame
    if n == 0:
        return False
    f = audio[: n * frame].reshape(n, frame)
    rms = np.sqrt((f * f).mean(axis=1))
    return int((rms > max(0.008, noise * 3.0)).sum()) >= min_frames


def wav_to_array(path: str) -> np.ndarray:
    import soundfile as sf

    data, sr = sf.read(path, dtype="float32", always_2d=True)
    data = data.mean(axis=1)
    if sr != SR:
        # Simple linear resample, good enough for tests.
        n = int(len(data) * SR / sr)
        data = np.interp(np.linspace(0, len(data) - 1, n), np.arange(len(data)), data).astype(np.float32)
    return data
