import AppKit
import ApplicationServices
import Carbon.HIToolbox

/// Learns from edits the user makes to pasted dictation.
/// Reads the focused field through Accessibility before and after the paste, remembers where the
/// pasted text sits (text before it and text after it), and later reads the field again to see
/// what the pasted span turned into. All AX calls run on the main thread with a short timeout.
final class EditLearner {
    var send: (([String: Any]) -> Void)?

    private static let maxChars = 20_000
    private static let checkDelay: TimeInterval = 30

    private struct Pending {
        let raw: String
        let pasted: String
        let prefix: [Unicode.Scalar]
        let suffix: [Unicode.Scalar]
        let element: AXUIElement
    }

    private var pending: Pending?
    private var deadline: Timer?
    private var focusWatch: Timer?

    // MARK: before and after the paste

    /// Everything read before the paste. Nil when the field is not safe or not readable.
    struct Snapshot {
        let element: AXUIElement
        let value: String
    }

    /// Call right before pasting. Resolves any older pending record first.
    func snapshotBeforePaste(raw: Bool) -> Snapshot? {
        checkPending()
        guard Settings.learnFromEdits, !raw, !IsSecureEventInputEnabled() else { return nil }
        guard let el = Self.focusedElement(), let v = Self.readValue(el) else { return nil }
        return Snapshot(element: el, value: v)
    }

    /// Call right after posting the paste. Waits about 300 ms, then reads the field again.
    func trackPaste(_ snap: Snapshot?, raw: String, pasted: String) {
        guard let snap else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { [weak self] in
            guard let self, Settings.learnFromEdits else { return }
            guard let v1 = Self.readValue(snap.element) else { return }
            let v0 = Array(snap.value.unicodeScalars)
            let a = Array(v1.unicodeScalars)
            let p = Array(pasted.unicodeScalars)
            guard a.count == v0.count + p.count else { return } // reformatted, or the paste did not land
            let (pre, suf) = Self.commonEnds(v0, a)
            guard Array(a[pre..<(a.count - suf)]) == p else { return }
            self.pending = Pending(raw: raw, pasted: pasted,
                                   prefix: Array(a[..<pre]), suffix: Array(a[(a.count - suf)...]),
                                   element: snap.element)
            self.startTimers()
        }
    }

    // MARK: checking

    private func startTimers() {
        stopTimers()
        deadline = Timer.scheduledTimer(withTimeInterval: Self.checkDelay, repeats: false) { [weak self] _ in
            self?.checkPending()
        }
        // One cheap focus compare per second while a record is pending.
        focusWatch = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            guard let self, let p = self.pending else { return }
            if let now = Self.focusedElement(), CFEqual(now, p.element) { return }
            self.checkPending()
        }
    }

    private func stopTimers() {
        deadline?.invalidate(); deadline = nil
        focusWatch?.invalidate(); focusWatch = nil
    }

    /// Reads the field once, sends feedback if the pasted span was meaningfully edited, drops the record.
    func checkPending() {
        guard let p = pending else { return }
        pending = nil
        stopTimers()
        guard Settings.learnFromEdits, !IsSecureEventInputEnabled(),
              let v2 = Self.readValue(p.element) else { return }
        let a = Array(v2.unicodeScalars)
        guard a.count >= p.prefix.count + p.suffix.count,
              a.starts(with: p.prefix),
              a.suffix(p.suffix.count).elementsEqual(p.suffix) else { return }
        let edited = String(String.UnicodeScalarView(a[p.prefix.count..<(a.count - p.suffix.count)]))
        guard edited != p.pasted, !edited.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              Self.sharesHalfTheWords(edited, p.pasted) else { return }
        send?(["type": "feedback", "kind": "dictation_edit",
               "raw": p.raw, "pasted": p.pasted, "edited": edited])
    }

    /// Forget the record without sending (setting turned off).
    func discard() {
        pending = nil
        stopTimers()
    }

    // MARK: helpers

    private static func sharesHalfTheWords(_ edited: String, _ pasted: String) -> Bool {
        let split: (String) -> [String] = {
            $0.lowercased().split(whereSeparator: { $0.isWhitespace }).map(String.init)
        }
        let e = split(edited)
        guard !e.isEmpty else { return false }
        let p = Set(split(pasted))
        return e.filter { p.contains($0) }.count * 2 >= e.count
    }

    /// Common prefix length, then common suffix length that does not overlap the prefix.
    private static func commonEnds(_ x: [Unicode.Scalar], _ y: [Unicode.Scalar]) -> (Int, Int) {
        let n = min(x.count, y.count)
        var pre = 0
        while pre < n && x[pre] == y[pre] { pre += 1 }
        var suf = 0
        while suf < n - pre && x[x.count - 1 - suf] == y[y.count - 1 - suf] { suf += 1 }
        return (pre, suf)
    }

    private static func focusedElement() -> AXUIElement? {
        let sys = AXUIElementCreateSystemWide()
        AXUIElementSetMessagingTimeout(sys, 0.2)
        var ref: CFTypeRef?
        guard AXUIElementCopyAttributeValue(sys, kAXFocusedUIElementAttribute as CFString, &ref) == .success,
              let r = ref, CFGetTypeID(r) == AXUIElementGetTypeID() else { return nil }
        return (r as! AXUIElement)
    }

    /// The element's text, or nil if it is secure, not text, or longer than the limit.
    private static func readValue(_ el: AXUIElement) -> String? {
        AXUIElementSetMessagingTimeout(el, 0.2)
        var role: CFTypeRef?
        if AXUIElementCopyAttributeValue(el, kAXRoleAttribute as CFString, &role) == .success,
           (role as? String) == "AXSecureTextField" { return nil }
        var sub: CFTypeRef?
        if AXUIElementCopyAttributeValue(el, kAXSubroleAttribute as CFString, &sub) == .success,
           (sub as? String) == "AXSecureTextField" { return nil }
        var count: CFTypeRef?
        if AXUIElementCopyAttributeValue(el, kAXNumberOfCharactersAttribute as CFString, &count) == .success,
           let n = count as? Int, n > maxChars { return nil }
        var val: CFTypeRef?
        guard AXUIElementCopyAttributeValue(el, kAXValueAttribute as CFString, &val) == .success,
              let s = val as? String, s.utf16.count <= maxChars else { return nil }
        return s
    }
}
