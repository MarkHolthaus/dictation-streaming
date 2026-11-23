# Engine protocol

The Mac app (Swift, `app/`) talks to the engine (Python, `engine/`) over TCP on `127.0.0.1:47321`.
Each message is one line of JSON ending in `\n`, both directions.

## Dictation

App to engine:

| Message | When |
|---|---|
| `{"type":"start","mode":"clean","id":7}` | Hotkey pressed. `mode` is `clean` (default) or `raw`. `id` counts up per hold. |
| `{"type":"audio","pcm16":"<base64>"}` | About every 100 ms while held. 16 kHz, mono, signed 16-bit little-endian PCM. |
| `{"type":"stop"}` | Hotkey released. |
| `{"type":"cancel"}` | Esc pressed while held. Engine drops the session, sends nothing. |

Engine to app:

| Message | Meaning |
|---|---|
| `{"type":"partial","text":"..."}` | Running cleaned text so far. App may show it in a small overlay. |
| `{"type":"final","text":"...","raw":"...","ms":123,"id":7}` | Paste `text`. `ms` = time from `stop` to this message. `id` echoes `start`, so the app drops a late result from an earlier hold. |
| `{"type":"error","message":"..."}` | Something failed. App shows it, pastes nothing. |

## Typing autocorrect

App to engine, on space or punctuation after a word:

`{"type":"word","word":"awya","before":"up to 200 chars of text before the word"}`

Engine to app:

`{"type":"word_result","word":"awya","fix":"away"}`. `fix` is `null` when the word is fine.
The app only swaps if the user has not typed since it sent the request.

## Feedback

App to engine:

- `{"type":"feedback","kind":"dictation_edit","raw":"...","pasted":"...","edited":"..."}` when the user edits pasted text within 30 s (app reads the field through Accessibility when it can).
- `{"type":"feedback","kind":"undo_fix","word":"awya","fix":"away"}` when the user presses backspace right after an autocorrect swap.

The engine appends feedback to `data/raw/feedback.jsonl` (gitignored; `DICTATION_FEEDBACK` overrides) for nightly training.

## Meetings

Independent of dictation: own messages, own session on the engine. One meeting per connection.

App to engine:

| Message | When |
|---|---|
| `{"type":"meeting_start","system":true}` | Start/Stop menu item. `system` is false when Screen Recording was denied (mic only). |
| `{"type":"meeting_audio","source":"mic","pcm16":"<base64>"}` | About every 500 ms. `source` is `mic` or `system`. 16 kHz mono PCM16, same as dictation. |
| `{"type":"meeting_speaker","name":"Dave"}` | The user picked who is talking. The engine stamps it on the audio clock. |
| `{"type":"meeting_stop"}` | Stop. If the connection drops mid-meeting the engine finishes and saves anyway. |

Engine to app: `{"type":"meeting_status","state":"recording|transcribing|summarizing"}`, then
`{"type":"meeting_done","transcript":"<path>","notes":"<path>","ok":true,"error":"","title":"..."}`.
`ok` is false when the local model failed; the transcript is still saved.

Output: two notes in `~/Documents/Dictation Meetings/` (set `MEETING_VAULT_DIR` to change it):
`YYYY-MM-DD HHMM Meeting.md` with summary, notes, to-dos and action items, and
`... Meeting transcript.md`. Audio and `transcript.json` go to
`data/raw/meetings/<stamp>/` (gitignored). Summary model: Ollama `qwen2.5:14b` (`MEETING_MODEL`).

## Hub (status and meeting review)

Plain request/response. The reply has the same `type`. All four work on any connection, with or
without a meeting in progress (a meeting still recording has no `transcript.json` yet, so it is
not listed).

| Request | Reply |
|---|---|
| `{"type":"status"}` | `{"type":"status","uptime_s":int,"asr_bits":8,"models":{"asr":bool,"tagger":bool,"context":bool,"editor":bool},"counts":{"dictations":int,"fixes":int,"meetings":int},"errors":[{"t":"ISO8601","where":"asr\|tagger\|context\|editor\|meeting\|server","msg":"..."}]}` |
| `{"type":"meeting_list"}` | `{"type":"meeting_list","meetings":[{"stamp":"2026-11-02_1430","title":"...","date":"ISO8601","duration_s":int,"speakers":["..."],"notes":"<path or empty>","transcript":"<path or empty>","untagged":int}]}`, newest first |
| `{"type":"meeting_get","stamp":"..."}` | `{"type":"meeting_get","stamp":"...","segments":[{"i":int,"start":float,"end":float,"source":"mic\|system","speaker":"...","text":"..."}],"summary":"<markdown of the notes note>"}`. Unknown stamp: empty `segments`, empty `summary`, plus `"error":"meeting not found"`. |
| `{"type":"meeting_retag","stamp":"...","assignments":[{"i":3,"speaker":"Dave"}],"rename":{"Them":"Dave"}}` | `{"type":"meeting_retag","ok":bool,"error":""}` |

- `models` is what is loaded right now. `editor` loads on demand, so false is normal. `counts` reset when the engine starts (`fixes` = autocorrect swaps sent).
- `errors` is the last 50 caught failures or fallbacks (for example tagger missing, so rule cleanup), oldest first.
- `stamp` is the `data/raw/meetings/` folder name with the space as `_`. Anything else (path separators, `..`) is rejected.
- `untagged` counts segments still labelled `Them` (unlabelled system audio).
- Retag applies `assignments` (by segment `i`) first, then `rename` across all segments. It rewrites `transcript.json` and the speaker labels in the notes-folder `... Meeting transcript.md`, each via a temp file and rename. Only labels change, never the text. The summary is not re-run, so the notes note keeps its old names. On any bad input nothing is written and `ok` is false.

## Health

`{"type":"ping"}` gets `{"type":"pong","models":{"asr":"...","tagger":"..."}}`.
