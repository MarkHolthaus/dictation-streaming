"""Benchmark: make speech with macOS `say`, stream it in real time chunks, time the tail.

Run from the repo root in a normal terminal (needs the GPU):
    .venv/bin/python -m engine.bench_asr [--bits 8|0]
Writes results to build/bench_asr.txt.
"""

from __future__ import annotations

import argparse
import os
import resource
import subprocess
import tempfile
import time

import numpy as np

from engine.asr import ASR, SR, DictationSession, wav_to_array

PHRASES = [
    "So I think we should meet at three on Tuesday, um, actually no, Wednesday.",
    "Um, so the plan is, uh, the plan is to get the dictation app running on the Mac first. Then the box trains it at night.",
    "I talked to him, her, her about the Acme2Go blog post and she said it's, like, fine.",
    "Okay. Um, yeah. Actually, no. Let's push the launch to next week and finish the ABCD101 homework tonight.",
]


def make_audio(text: str, tmp: str, i: int) -> np.ndarray:
    aiff = os.path.join(tmp, f"p{i}.aiff")
    wav = os.path.join(tmp, f"p{i}.wav")
    subprocess.run(["say", "-o", aiff, text], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", aiff, wav], check=True)
    return wav_to_array(wav)


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6  # bytes on macOS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bits", type=int, default=8)
    args = ap.parse_args()
    import mlx.core as mx

    out = []
    t0 = time.perf_counter()
    asr = ASR(bits=args.bits or None)
    out.append(f"model {asr.model_id} bits={args.bits} load {time.perf_counter()-t0:.1f}s")
    out.append(f"mlx active memory {mx.get_active_memory()/1e6:.0f} MB, peak {mx.get_peak_memory()/1e6:.0f} MB, process max RSS {rss_mb():.0f} MB")

    with tempfile.TemporaryDirectory() as tmp:
        clips = [make_audio(p, tmp, i) for i, p in enumerate(PHRASES)]
        # One long clip: all phrases with 0.8 s pauses, like a brain dump.
        gap = np.zeros(int(0.8 * SR), dtype=np.float32)
        long = np.concatenate([x for c in clips for x in (c, gap)])
        clips.append(long)
        PHRASES.append(" ".join(PHRASES))

        for text, audio in zip(PHRASES, clips):
            # Full-clip accuracy reference
            t = time.perf_counter()
            full = asr.transcribe(audio)
            full_ms = (time.perf_counter() - t) * 1000

            # Streamed: feed 100 ms chunks at real-time pace
            sess = DictationSession(asr)
            chunk = SR // 10
            for i in range(0, len(audio), chunk):
                sess.add_audio(audio[i : i + chunk])
                time.sleep(0.1)
            t = time.perf_counter()
            segs = sess.finish()
            tail_ms = (time.perf_counter() - t) * 1000
            out.append("")
            out.append(f"said:     {text}")
            out.append(f"full:     {full}  ({full_ms:.0f} ms for {len(audio)/SR:.1f}s audio)")
            out.append(f"streamed: {' '.join(segs)}  ({len(segs)} segments)")
            out.append(f"time after stop: {tail_ms:.0f} ms")

    out.append("")
    out.append(f"final mlx peak {mx.get_peak_memory()/1e6:.0f} MB, process max RSS {rss_mb():.0f} MB")
    os.makedirs("build", exist_ok=True)
    with open(f"build/bench_asr_{args.bits}.txt", "w") as f:
        f.write("\n".join(out) + "\n")
    print("\n".join(out))
    print("BENCH_DONE")


if __name__ == "__main__":
    main()
