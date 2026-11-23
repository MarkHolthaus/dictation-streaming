# Dictation app notes

Protocol: unchanged, see `../PROTOCOL.md`.

## Build
- `cd app && swift build -c release`, or `./bundle.sh` for `build/Dictation.app` (ad-hoc signed).
- Test without the real engine: `python3 tools/mock_engine.py`, then open the app.
- Not verified here: the build was proven with `swiftc` (Swift 5 language mode, zero warnings). The sandbox blocked SwiftPM file writes. Run `swift build -c release` once on the Mac.

## Design
- One CGEventTap (session, default tap) handles fn (flagsChanged, keycode 63, `.maskSecondaryFn`; press counts after 250 ms so fn chords still work), keyDown and mouse clicks.
- Synthetic events carry `eventSourceUserData = kSyntheticMagic` and are ignored by the tap.
- Mic is kept warm by default (menu: "Keep mic warm") with a 400 ms pre-roll. The orange mic dot stays on while warm. Turn it off to start the mic only on key press.
- Taps shorter than 150 ms send `start` then `cancel`. They end capture at once (no tail).
- On a normal release the mic keeps streaming a 250 ms tail (`Coordinator.tailSeconds`): the tap buffer still in flight, mic latency (more on AirPods) and the soft end of the last word. Then the capture is flushed and `stop` is queued behind the last chunk, so the engine finalizes as soon as the tail lands. The paste waits about 250 ms longer than before, no more. A new hold cancels a pending tail.
- Engine side, the last piece is kept if it has at least 4 loud 20 ms frames (80 ms), so a short last word survives and a 20 ms click does not become "Yeah.".
- While fn is held, a 250 ms timer polls the real key state and releases if the key-up was missed.
- Autocorrect skips a swap if a keyDown is still in the event queue (HID last-keyDown time is newer than the last one the tap handled). Fixes keep the original capitalization.
- Mic tap uses the node's own format (`format: nil`); the converter is rebuilt when the buffer format changes (AirPods, new default mic).
- Autocorrect swaps only if no key was typed after the `word` request. Backspace right after a swap undoes it and sends `undo_fix`.
- Return, Tab, arrows, Esc, Home/End, paging, Cmd/Ctrl shortcuts, mouse clicks and app switches reset the word buffer.

## Edit feedback (`EditLearner.swift`)
- Menu toggle "Learn from my edits" (default on, `Settings.learnFromEdits`).
- Right before paste the focused element's text is read through Accessibility (V0). About 300 ms after paste it is read again (V1).
- The common prefix and common suffix (non-overlapping) of V0 and V1 mark where the paste sits. If the text between them is not exactly the pasted text, the record is dropped quietly (the app hides its text or reformatted the paste).
- One pending record is kept: raw, pasted, prefix, suffix, element. It is checked at the next dictation press, after 30 s, or when the focused element changes (a 1 s compare timer), whichever is first. The stored element is re-read (V2). If V2 still starts with the prefix and ends with the suffix, the middle is `edited`.
- `feedback` (`dictation_edit`) is sent only if `edited` differs from `pasted`, is not blank, and at least half of its words appear in `pasted`. Then the record is dropped.
- Skipped for secure fields (`IsSecureEventInputEnabled`, `AXSecureTextField`), fields over 20,000 characters, raw mode, and when the setting is off.
- All AX calls run on the main thread with a 0.2 s messaging timeout. No blocking loops.
- Cannot capture: apps with no AX text value (many Electron, canvas and terminal views), apps that reformat or autocorrect the paste, edits made after the 30 s window or after the field lost focus without the app keeping the element alive, a V0 text equal to the start of the paste at the caret (prefix ambiguity drops it), and edits that rewrite most of the text (under half the words shared).

## Known gaps / TODO
- Words ending in an apostrophe (quotes) are treated as part of the word.
- Return/Tab never trigger a word check (the swap would have to cross a line break).
- With the mic not kept warm, the 250 ms fn wait plus mic start may clip the first word. Keep mic warm on.
- A stale `final` after a new press is dropped.

## Dashboard (`Sources/Dictation/Hub/`)
- Menu "Open Dashboard..." opens one reusable window (`HubWindowController`); closing it only hides it. Sidebar: Overview, Meetings, Settings, Problems.
- `HubModel` polls the engine's `status` every 5 s while the window is visible (stops when closed, minimized or covered). Replies come from `EngineClient.request(_:timeout:)`: next reply with the same `type`, one in flight per type, nil on timeout or disconnect.
- Settings changes go through `HubModel` with the same effects as the menu, then `AppDelegate.refresh()`, which posts `.hubStateChanged` so the window re-reads state when the menu changes things.
- Problems: engine down (red), no `status` answer or `errors` from the engine (amber), Accessibility / Input Monitoring / Microphone missing (red), Screen Recording missing (amber, meetings fall back to mic only). Each permission has a button to its System Settings pane. A dot shows on Overview and Problems.
- Meetings tab: `MeetingsTab(controller:request:)` lives in `Hub/MeetingsView.swift`.
- Not checked on screen: the window layout and the System Settings deep links.

## Auto-detect meetings (`MeetingDetector.swift`)
- `Settings.autoDetectMeetings`: `off`, `ask` (default), `auto`. Menu submenu "Auto-detect meetings" and the Settings tab picker both go through `refresh()`; Overview shows "Auto-detect".
- Signal: CoreAudio process list (`kAudioHardwarePropertyProcessObjectList`, macOS 14.4+), polled every 2 s. A process counts if its input is running, its pid is not ours (so dictation and the warm mic never count) and its bundle id starts with Zoom, Teams, Webex, Slack, FaceTime, Chrome, Safari, Arc, Edge, Brave or Firefox (helpers match their parent). No audio is read, no window titles, no extra permission.
- Debounce: 8 s on the mic before it counts, 20 s quiet before it is over.
- Ask: alert "Looks like a meeting in X" with Start / Not now (asked once per mic session). Auto: starts at once and shows the overlay.
- Never starts while a meeting is not idle or fn is held (retries next tick). Only a recording started by the detector is stopped, 10 s after the end is seen, and only if the call has not resumed. Hand-started meetings are never auto-stopped.
- Limits: a browser using the mic for something else (dictation sites, voice notes) looks like a meeting. Not tested against a live call.
