import AppKit
import AVFoundation
import Carbon.HIToolbox

/// Ties the hotkey, mic, engine, overlay and paste together.
final class Coordinator {
    let engine = EngineClient()
    let audio = AudioCapture()
    let overlay = Overlay()
    let autocorrect = Autocorrect()
    let tap = EventTapController()
    let editLearner = EditLearner()
    let meeting = MeetingController()
    private(set) lazy var detector = MeetingDetector(meeting: meeting)

    private var held = false
    /// True from `start` until `stop`/`cancel` is sent. Separate from `held` so the tail chunks
    /// flushed on release (queued ahead of `stop`) are still sent.
    private var streaming = false
    private var heldWatch: Timer?
    private var cancelled = false
    private var awaitingFinal = false
    private var pressTime = Date()
    /// Mode at the time of the hold. Raw dictation is never used for edit feedback.
    private var pressedRaw = false
    private var finalTimeout: DispatchWorkItem?
    private var tapRetry: Timer?

    var onStateChange: (() -> Void)?
    var dictating: Bool { held || streaming }

    func start() {
        autocorrect.enabled = Settings.autocorrectEnabled
        autocorrect.send = { [engine] in engine.send($0) }
        editLearner.send = { [engine] in engine.send($0) }

        meeting.send = { [engine] in engine.send($0) }
        meeting.connected = { [engine] in engine.connected }
        meeting.notify = { [weak self] text, secs in
            self?.overlay.show(text: text, color: .systemBlue)
            self?.overlay.hide(after: secs)
        }
        meeting.onChange = { [weak self] in self?.onStateChange?() }
        detector.isDictating = { [weak self] in self?.dictating ?? false }
        detector.notify = meeting.notify
        detector.start()

        engine.onMessage = { [weak self] in self?.handle($0) }
        engine.onStateChange = { [weak self] in self?.onStateChange?() }
        engine.start()

        audio.onChunk = { [weak self] data in
            guard let self, self.streaming else { return }
            self.engine.send(["type": "audio", "pcm16": data.base64EncodedString()])
        }

        tap.onFlagsChanged = { [weak self] e in self?.flagsChanged(e) }
        tap.onKeyDown = { [weak self] e in self?.keyDown(e) ?? false }
        tap.onMouseDown = { [weak self] in self?.autocorrect.reset() }
        NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main
        ) { [weak self] _ in self?.autocorrect.reset() }

        requestPermissions()
        installTapWithRetry()

        AVCaptureDevice.requestAccess(for: .audio) { [weak self] granted in
            guard granted else { return }
            DispatchQueue.main.async {
                if Settings.keepMicWarm { try? self?.audio.startEngine() }
            }
        }
    }

    var tapInstalled: Bool { tap.isInstalled }

    func setKeepWarm(_ on: Bool) {
        Settings.keepMicWarm = on
        if on { try? audio.startEngine() } else if !held && tailWork == nil { audio.stopEngine() }
    }

    // MARK: permissions

    private func requestPermissions() {
        let opts = ["AXTrustedCheckOptionPrompt": true] as CFDictionary
        _ = AXIsProcessTrustedWithOptions(opts)
        if !CGPreflightListenEventAccess() { _ = CGRequestListenEventAccess() }
    }

    private func installTapWithRetry() {
        if tap.install() { tapRetry?.invalidate(); tapRetry = nil; onStateChange?(); return }
        tapRetry = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] t in
            guard let self else { return }
            if self.tap.install() { t.invalidate(); self.tapRetry = nil; self.onStateChange?() }
        }
        onStateChange?()
    }

    // MARK: hotkey

    /// Keys that dictate while held alone, with the flag bit that says each one is down.
    /// fn (globe) on Apple keyboards. Right Option and Right Command for keyboards whose fn
    /// never reaches macOS (Lofree Flow and most other third-party boards handle fn inside).
    private static let holdKeys: [Int64: UInt64] = [
        63: CGEventFlags.maskSecondaryFn.rawValue,
        61: 0x40, // NX_DEVICERALTKEYMASK, right Option
        54: 0x10, // NX_DEVICERCMDKEYMASK, right Command
    ]
    /// The hold key that started the pending or current hold.
    private var holdKey: Int64 = 63

    private func flagsChanged(_ e: CGEvent) {
        let code = e.getIntegerValueField(.keyboardEventKeycode)
        guard let bit = Self.holdKeys[code] else { return }
        let down = e.flags.rawValue & bit != 0
        if down {
            guard !held, fnDelay == nil else { return }
            holdKey = code
            // Wait briefly so fn+arrow, Option+letter, Command+key and the like still work as chords.
            let work = DispatchWorkItem { [weak self] in self?.fnDelay = nil; self?.press() }
            fnDelay = work
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.25, execute: work)
        } else {
            guard code == holdKey else { return }
            fnDelay?.cancel(); fnDelay = nil
            if held { release() }
        }
    }

    /// True while the key that started this hold is still down. Reads the modifier flag, not
    /// `keyState`: `keyState` answers false for held modifier keys, which ended every
    /// right Option or right Command hold within 250 ms. The real key-up still comes
    /// through `flagsChanged`; this is only the backstop for a missed release.
    private func holdKeyIsDown() -> Bool {
        let flags = CGEventSource.flagsState(.combinedSessionState)
        switch holdKey {
        case 61: return flags.contains(.maskAlternate)
        case 54: return flags.contains(.maskCommand)
        default: return flags.contains(.maskSecondaryFn)
        }
    }

    private var fnDelay: DispatchWorkItem?

    private func keyDown(_ e: CGEvent) -> Bool {
        let code = Int(e.getIntegerValueField(.keyboardEventKeycode))
        if let w = fnDelay { w.cancel(); fnDelay = nil; return false } // fn chord: leave it to the system
        if held {
            if code == kVK_Escape { cancel(); return true }
            cancel() // fn plus another key is a shortcut, not dictation
            return false
        }
        return autocorrect.handleKeyDown(e)
    }

    private var sessionId = 0

    private func press() {
        guard Settings.dictationEnabled else { return }
        if let t = tailWork {
            // A new hold inside the previous hold's tail window. The fn delay (250 ms) makes this
            // near impossible, but never let the old tail end the new session: drop the old one.
            t.cancel(); tailWork = nil
            engine.send(["type": "cancel"])
        }
        held = true
        cancelled = false
        awaitingFinal = false
        pressTime = Date()
        pressedRaw = Settings.mode == "raw"
        editLearner.checkPending()
        finalTimeout?.cancel()
        autocorrect.reset()
        sessionId += 1
        guard engine.send(["type": "start", "mode": Settings.mode, "id": sessionId]) else {
            held = false
            overlay.show(text: "Engine not connected", color: .systemGray)
            overlay.hide(after: 2)
            return
        }
        streaming = true
        do {
            try audio.beginCapture()
        } catch {
            held = false
            streaming = false
            engine.send(["type": "cancel"])
            overlay.show(text: AudioCapture.friendlyMessage(for: error), color: .systemOrange)
            overlay.hide(after: 3)
            return
        }
        overlay.show(text: "Listening...")
        startHeldWatch()
    }

    /// A release can be missed (tap disabled by timeout, secure input, app switch mid-press).
    /// Poll the real key state while held so dictation never sticks on.
    private func startHeldWatch() {
        heldWatch?.invalidate()
        heldWatch = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] t in
            guard let self, self.held else { t.invalidate(); return }
            if !self.holdKeyIsDown() { self.release() }
        }
    }

    /// Sends a session-ending message behind any audio chunks already queued on main.
    private func endStream(_ type: String) {
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.engine.send(["type": type])
            if !self.held { self.streaming = false }
        }
    }

    /// Audio still in flight at key-up (tap buffer, mic latency, more on Bluetooth) plus the
    /// soft end of the last word. Capture keeps running this long after release.
    private let tailSeconds = 0.25
    private var tailWork: DispatchWorkItem?

    private func release() {
        held = false
        heldWatch?.invalidate(); heldWatch = nil
        autocorrect.reset() // keys typed while held were not tracked
        if cancelled { return } // cancel() already ended capture
        if Date().timeIntervalSince(pressTime) < 0.15 {
            // A tap, not dictation: end at once and discard.
            audio.endCapture(keepWarm: Settings.keepMicWarm)
            streaming = false
            DispatchQueue.main.async { [engine] in engine.send(["type": "cancel"]) }
            overlay.hide()
            return
        }
        awaitingFinal = true
        overlay.setText("Working...")
        // Keep streaming the tail, then flush and send `stop` queued behind the last chunk.
        // The engine finalizes as soon as `stop` arrives, so the paste waits only for the tail.
        let tail = DispatchWorkItem { [weak self] in
            guard let self else { return }
            self.tailWork = nil
            self.audio.endCapture(keepWarm: Settings.keepMicWarm)
            self.endStream("stop")
        }
        tailWork = tail
        DispatchQueue.main.asyncAfter(deadline: .now() + tailSeconds, execute: tail)
        let work = DispatchWorkItem { [weak self] in
            guard let self, self.awaitingFinal else { return }
            self.awaitingFinal = false
            self.overlay.show(text: "Engine did not answer", color: .systemOrange)
            self.overlay.hide(after: 2.5)
        }
        finalTimeout = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 20, execute: work)
    }

    private func cancel() {
        tailWork?.cancel(); tailWork = nil
        cancelled = true
        held = false
        streaming = false
        heldWatch?.invalidate(); heldWatch = nil
        audio.endCapture(keepWarm: Settings.keepMicWarm)
        autocorrect.reset()
        DispatchQueue.main.async { [engine] in engine.send(["type": "cancel"]) }
        overlay.hide()
    }

    // MARK: engine messages

    private func handle(_ msg: [String: Any]) {
        switch msg["type"] as? String {
        case "partial":
            if held || awaitingFinal, let t = msg["text"] as? String { overlay.setText(t) }
        case "final":
            // Drop a late result from an earlier hold.
            if let id = msg["id"] as? Int, id != sessionId { return }
            guard awaitingFinal else { return }
            awaitingFinal = false
            finalTimeout?.cancel()
            overlay.hide()
            if let text = msg["text"] as? String, !text.isEmpty {
                autocorrect.reset() // the pasted text is not in the word buffer
                let snap = editLearner.snapshotBeforePaste(raw: pressedRaw)
                Paster.paste(text)
                editLearner.trackPaste(snap, raw: msg["raw"] as? String ?? "", pasted: text)
            }
        case "error":
            awaitingFinal = false
            finalTimeout?.cancel()
            overlay.show(text: msg["message"] as? String ?? "Engine error", color: .systemOrange)
            overlay.hide(after: 3)
        case "word_result":
            if let w = msg["word"] as? String {
                autocorrect.handleResult(word: w, fix: msg["fix"] as? String)
            }
        default:
            meeting.handle(msg)
        }
    }
}
