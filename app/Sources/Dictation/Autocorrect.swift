import AppKit
import Carbon.HIToolbox

/// Typing autocorrect. Tracks the current word, asks the engine on word end, swaps if nothing was typed since.
final class Autocorrect {
    var enabled = true
    var send: (([String: Any]) -> Bool)?

    private var word = ""
    private var recent = ""
    private var typedSeq = 0
    /// Uptime when the tap last saw a real keyDown. Used to spot keys still in flight.
    private var lastKeyUptime: TimeInterval = 0

    private struct Pending { let word: String; let delim: String; let seq: Int }
    private var pendings: [Pending] = []

    private struct Swap { let original: String; let fix: String; let delim: String }
    private var lastSwap: Swap?

    /// Keys that end the current context (arrows, return, tab, esc, home/end, paging, F-keys, forward delete).
    private let resetKeys: Set<Int> = [
        kVK_LeftArrow, kVK_RightArrow, kVK_UpArrow, kVK_DownArrow,
        kVK_Return, kVK_ANSI_KeypadEnter, kVK_Tab, kVK_Escape,
        kVK_Home, kVK_End, kVK_PageUp, kVK_PageDown, kVK_ForwardDelete,
        kVK_F1, kVK_F2, kVK_F3, kVK_F4, kVK_F5, kVK_F6, kVK_F7, kVK_F8, kVK_F9, kVK_F10,
        kVK_F11, kVK_F12, kVK_F13, kVK_F14, kVK_F15, kVK_F16, kVK_F17, kVK_F18, kVK_F19, kVK_F20,
    ]

    func reset() {
        word = ""
        recent = ""
        lastSwap = nil
        typedSeq += 1
        pendings.removeAll()
    }

    /// Returns true when the key should be swallowed (undo).
    func handleKeyDown(_ event: CGEvent) -> Bool {
        guard enabled else { return false }
        lastKeyUptime = ProcessInfo.processInfo.systemUptime
        // Held keys: the buffer no longer matches the screen (and a stale lastSwap must not undo).
        if event.getIntegerValueField(.keyboardEventAutorepeat) != 0 {
            reset()
            return false
        }
        let flags = event.flags
        if flags.contains(.maskCommand) || flags.contains(.maskControl) || IsSecureEventInputEnabled() {
            reset()
            return false
        }
        typedSeq += 1
        let code = Int(event.getIntegerValueField(.keyboardEventKeycode))

        // Option-Delete removes a whole word, not one character.
        if code == kVK_Delete && flags.contains(.maskAlternate) {
            reset()
            return false
        }
        if code == kVK_Delete {
            if let s = lastSwap {
                lastSwap = nil
                undo(s)
                return true
            }
            if !word.isEmpty { word.removeLast() }
            if !recent.isEmpty { recent.removeLast() }
            // Backspaced over the delimiter: the cursor is back at the end of the previous word.
            if word.isEmpty { word = String(recent.reversed().prefix(while: Self.isWordChar).reversed()) }
            return false
        }
        lastSwap = nil
        if resetKeys.contains(code) {
            reset()
            return false
        }

        var length = 0
        var chars = [UniChar](repeating: 0, count: 8)
        event.keyboardGetUnicodeString(maxStringLength: 8, actualStringLength: &length, unicodeString: &chars)
        let s = String(utf16CodeUnits: chars, count: length)
        guard s.count == 1, let ch = s.first, !ch.isNewline,
              !(ch.unicodeScalars.first.map { $0.value < 32 } ?? true) else {
            reset()
            return false
        }

        if Self.isWordChar(ch) {
            word.append(ch)
            recent.append(ch)
            trimRecent()
            return false
        }

        // Delimiter: space or punctuation.
        let finished = word
        let before = String(recent.dropLast(finished.count).suffix(200))
        recent.append(ch)
        trimRecent()
        word = ""
        if finished.count >= 2, finished.contains(where: { $0.isLetter }) {
            let p = Pending(word: finished, delim: String(ch), seq: typedSeq)
            pendings.append(p)
            if pendings.count > 4 { pendings.removeFirst() }
            _ = send?(["type": "word", "word": finished, "before": before])
        }
        return false
    }

    private static func isWordChar(_ ch: Character) -> Bool {
        ch.isLetter || ch.isNumber || ch == "'" || ch == "\u{2019}"
    }

    /// Keeps "Teh" -> "The" and "TEH" -> "THE" when the engine answers in lower case.
    private static func matchCase(_ fix: String, to original: String) -> String {
        let letters = original.filter { $0.isLetter }
        if letters.count >= 2, letters.allSatisfy({ $0.isUppercase }) { return fix.uppercased() }
        if let f = original.first, f.isUppercase, let g = fix.first, g.isLowercase {
            return g.uppercased() + fix.dropFirst()
        }
        return fix
    }

    /// True if a keyDown reached the window server after the last one the tap handled,
    /// meaning the user typed something that is still in the event queue.
    private func keyInFlight() -> Bool {
        let since = CGEventSource.secondsSinceLastEventType(.hidSystemState, eventType: .keyDown)
        let lastHID = ProcessInfo.processInfo.systemUptime - since
        return lastHID > lastKeyUptime + 0.005
    }

    func handleResult(word w: String, fix rawFix: String?) {
        guard let idx = pendings.firstIndex(where: { $0.word == w }) else { return }
        let p = pendings.remove(at: idx)
        guard let rawFix, !rawFix.isEmpty, rawFix != w, enabled, p.seq == typedSeq,
              !IsSecureEventInputEnabled(), !keyInFlight() else { return }
        let fix = Self.matchCase(rawFix, to: w)
        guard fix != w else { return }
        replace(deleteCount: w.count + p.delim.count, with: fix + p.delim)
        if recent.hasSuffix(w + p.delim) {
            recent = String(recent.dropLast(w.count + p.delim.count)) + fix + p.delim
        }
        lastSwap = Swap(original: w, fix: fix, delim: p.delim)
    }

    private func undo(_ s: Swap) {
        replace(deleteCount: s.fix.count + s.delim.count, with: s.original + s.delim)
        if recent.hasSuffix(s.fix + s.delim) {
            recent = String(recent.dropLast(s.fix.count + s.delim.count)) + s.original + s.delim
        }
        _ = send?(["type": "feedback", "kind": "undo_fix", "word": s.original, "fix": s.fix])
    }

    private func trimRecent() {
        if recent.count > 260 { recent = String(recent.suffix(220)) }
    }

    // MARK: synthetic typing

    private func replace(deleteCount: Int, with text: String) {
        let src = CGEventSource(stateID: .hidSystemState)
        for _ in 0..<deleteCount { post(src, key: CGKeyCode(kVK_Delete), text: nil) }
        var units = Array(text.utf16)
        while !units.isEmpty {
            let n = min(16, units.count)
            post(src, key: 0, text: Array(units.prefix(n)))
            units.removeFirst(n)
        }
    }

    private func post(_ src: CGEventSource?, key: CGKeyCode, text: [UniChar]?) {
        for down in [true, false] {
            guard let e = CGEvent(keyboardEventSource: src, virtualKey: key, keyDown: down) else { continue }
            e.flags = []
            if var text { e.keyboardSetUnicodeString(stringLength: text.count, unicodeString: &text) }
            e.setIntegerValueField(.eventSourceUserData, value: kSyntheticMagic)
            e.post(tap: .cghidEventTap)
        }
    }
}
