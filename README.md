A macOS menu-bar app and local Python engine that turns held-key speech into cleaned text in any app, and fixes typos as you type.

Hold a key, talk, let go: the text is pasted a moment later, with fillers and self-corrections removed, and every model runs on your own Mac.

I wrote it to replace a paid dictation tool I used every day. I still use it daily. It is a personal tool, so expect rough edges (see Limits).

## Problem

Dictation tools either paste every "um" and false start, or send audio to a server. I wanted three things:

- Speech in, clean text out, with the delay after I let go kept short.
- Cleanup that can only delete words. It must never reword me.
- Everything local: no audio, text or keystrokes leave the machine.

## Decisions

- **Two processes.** A Swift menu-bar app owns the OS parts (key hooks, microphone, paste, Accessibility). A Python engine owns the models. They talk over `127.0.0.1:47321`, one JSON object per line. The protocol is in [PROTOCOL.md](PROTOCOL.md). MLX is Python-first and the event tap is Swift-first, so this split cost less than forcing either one into the other language.
- **Streaming transcription.** While the key is held, the engine cuts the audio at pauses and transcribes each piece as it arrives. When the key is released, only the last piece is left to do.
- **A tagger instead of a rewriting model.** A small token classifier (ELECTRA-small, int8 ONNX) labels each word keep, delete or needs-edit. Fillers, repeats and "Tuesday, actually no, Wednesday" become deletions. Only words flagged needs-edit ("make that Wednesday") go to a 0.6B edit model, and that model loads on demand. If the tagger is missing, a plain filler-word rule runs instead.
- **Autocorrect in layers.** (1) a typo map, (2) SymSpell, (3) an optional context check: a masked language model scores real-word mix-ups (then/than, form/from) and only swaps when another word in a small confusion set wins by a fixed margin, using the text to the left only. Each layer can say "leave it". Words you type on purpose, and words you undo a fix on, are never touched again.
- **No torch at runtime.** Training and export use torch. The running engine uses MLX, onnxruntime and tokenizers.
- **Learns from your corrections.** The app reports when you edit pasted text or undo an autocorrect. A nightly job turns those into training rows and swaps in a retrained tagger only if its holdout score holds.
- **Meeting tracker (extra).** Records mic and system audio, labels speakers from the menu, writes a transcript and notes. The summary comes from a local Ollama model.

## Stack

| Part | What |
|---|---|
| App | Swift 5.9, SwiftUI and AppKit, ScreenCaptureKit for system audio, macOS 14+ |
| Speech to text | Parakeet TDT 0.6B v2 on MLX (`mlx-community/parakeet-tdt-0.6b-v2`), 8-bit by default |
| Cleanup | ELECTRA-small tagger (ONNX int8), Qwen3 0.6B 4-bit edit model (MLX) |
| Autocorrect | typo map, SymSpell, distilroberta masked LM (ONNX int8) for the context layer |
| Data | wordfreq, the SymSpell frequency list, the system word list |

About 2,900 lines of Swift and 5,000 lines of Python.

## Install and run

Needs a Mac with Apple silicon, macOS 14 or newer, Python 3.12, and Xcode command line tools for the Swift build.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python engine/setup_data.py
```

`setup_data.py` builds the word lists and a small starter typo map into `data/` (gitignored). It uses no personal text. To use your own, put your messages in `data/raw/user_messages.jsonl` (one `{"text": "..."}` per line) and run `.venv/bin/python engine/build_typo_map.py`.

Start the engine in a normal terminal (the GPU is not available from a sandboxed shell). The first run downloads the speech model:

```bash
./run_engine.sh
```

Build and open the app, then grant the permissions it asks for:

```bash
cd app && ./bundle.sh && open build/Dictation.app
```

Hold fn, right Option or right Command, talk, and let go. To start the engine at login: `launchd/install.sh`.

Optional pieces:

- Context layer: `HF_HOME=.models/hf .venv/bin/python engine/export_context.py` writes the int8 model to `.models/context/`. Without it autocorrect runs layers 1 and 2 only.
- Tagger weights are **not** in this repo. Train your own with `tagger/make_data.py`, `tagger/train.py` and `tagger/export_onnx.py` (see [tagger/RESULTS.md](tagger/RESULTS.md)). `make_data.py` builds its training set from about 230 hand-written sentences and 47 templates, plus your own markdown notes if you set `DICTATION_NOTES_DIR`. Until then the engine uses the rule cleanup.
- Your own names and codes: edit `CORE` in `engine/vocab.py`.
- Notes folders and meetings: `DICTATION_NOTES_DIR`, `MEETING_VAULT_DIR` and `MEETING_ME` are described in the source and in PROTOCOL.md.

Tests that need no mic or GPU (run `engine/setup_data.py` first):

```bash
.venv/bin/python -m pytest -q engine/test_vocab.py engine/test_hub_api.py engine/test_autocorrect.py
```

The layer 3 checks skip without the context model. `tagger/test_tagger.py` is a script, not a pytest file, and needs the tagger weights.

## What I learned

- Keeping the tail short is mostly about the end of the hold, not the model: the mic buffer still in flight, device latency (worse on AirPods) and the soft end of the last word all arrive after the key comes up. The app keeps streaming for 250 ms after release.
- Audio devices change under you. A new default mic changes the buffer format, so the converter is rebuilt when it does, and a failed start (error -10868) gets a fresh `AVAudioEngine` and a few retries.
- A sandboxed shell has no Metal, so engine, benchmarks and tests that touch MLX have to run in a normal terminal.
- Signing by bundle identifier instead of by code hash keeps the Accessibility and Input Monitoring grants across rebuilds.
- "Only delete, never reword" is a constraint that simplifies testing: the expected output of the cleanup is the input with some words removed.
- I tuned the tagger's rule layer while looking at the first eval set's failures, so that set is a dev set, not a blind test. I wrote a second holdout after tuning. Both are small and hand-written.

## Limits

- Apple silicon and macOS 14+ only. English only.
- Permissions: Microphone, Accessibility (paste and the edit-learning read), Input Monitoring (the key hooks) and, for meetings only, Screen and System Audio Recording. Without them the app cannot hear or paste.
- With "keep mic warm" on, the orange microphone indicator stays on.
- The speech model and the optional models are downloaded from Hugging Face on first use. Parakeet is the largest download.
- The trained tagger and the context model are not included, and the typo map is a small starter. Quality out of the box is the rule cleanup plus SymSpell, not what I run daily.
- The app is ad-hoc signed, not notarized.
- Numbers in `tagger/RESULTS.md` were recorded during development on my data and my machine. I did not re-run them for this repo, and the autocorrect evaluation needs private messages that are not included.
- The meeting tracker expects a local Ollama model (default `qwen2.5:14b`, set with `MEETING_MODEL`).
- Not tested for this copy: the engine with real audio or the GPU, the Swift build, training the tagger, the context-model export, `launchd/install.sh`, and anything that needs the microphone or the permission prompts.

## Built with AI help

Much of the code was written with Claude Code, with me setting the design and checking the results.

## License

MIT, see [LICENSE](LICENSE).
