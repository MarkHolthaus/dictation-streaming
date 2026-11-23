import AppKit
import CoreAudio

/// Watches for a likely meeting and starts or offers `MeetingController` recording.
/// Uses only CoreAudio device state (which processes have an input stream running);
/// no audio is read and no window titles are looked at, so it needs no extra permission.
final class MeetingDetector {
    enum Mode: String { case off, ask, auto }

    /// Apps that count as a meeting when they hold the mic. Matched by bundle id prefix,
    /// so browser helper processes ("com.google.Chrome.helper") match their parent.
    private static let apps: [(prefix: String, name: String)] = [
        ("us.zoom.xos", "Zoom"),
        ("com.microsoft.teams", "Teams"),
        ("com.cisco.webex", "Webex"),
        ("Cisco-Systems.Spark", "Webex"),
        ("com.tinyspeck.slackmacgap", "Slack"),
        ("com.apple.FaceTime", "FaceTime"),
        ("com.google.Chrome", "Chrome"),
        ("com.apple.Safari", "Safari"),
        ("company.thebrowser.Browser", "Arc"),
        ("com.microsoft.edgemac", "Edge"),
        ("com.brave.Browser", "Brave"),
        ("org.mozilla.firefox", "Firefox"),
    ]

    static let startDebounce: TimeInterval = 8
    static let endDebounce: TimeInterval = 20
    static let stopGrace: TimeInterval = 10

    let meeting: MeetingController
    var isDictating: (() -> Bool)?
    var notify: ((String, TimeInterval) -> Void)?
    var onChange: (() -> Void)?

    private var timer: Timer?
    private var activeSince: Date?   // mic held by a meeting app since
    private var quietSince: Date?    // no meeting app on the mic since
    private var handled = false      // already prompted or started for this mic session
    private var autoStarted = false  // the running recording was started by us
    private var stopWork: DispatchWorkItem?
    private var prompting = false

    init(meeting: MeetingController) { self.meeting = meeting }

    func start() {
        timer = Timer(timeInterval: 2, repeats: true) { [weak self] _ in self?.tick() }
        RunLoop.main.add(timer!, forMode: .common)
    }

    /// Call when the setting changes.
    func settingChanged() {
        if Settings.autoDetectMeetings == "off" {
            activeSince = nil; quietSince = nil; handled = false; stopWork?.cancel(); stopWork = nil
        }
    }

    // MARK: polling

    private func tick() {
        guard Mode(rawValue: Settings.autoDetectMeetings) ?? .ask != .off else { return }
        let now = Date()
        // A recording the user started by hand is never ours to stop.
        if meeting.state == .idle { autoStarted = false }
        let app = meetingAppUsingMic()
        if let app {
            quietSince = nil
            stopWork?.cancel(); stopWork = nil
            if activeSince == nil { activeSince = now }
            if !handled, now.timeIntervalSince(activeSince!) >= Self.startDebounce { trigger(app) }
        } else {
            if quietSince == nil { quietSince = now }
            if now.timeIntervalSince(quietSince!) >= Self.endDebounce {
                activeSince = nil
                handled = false
                if autoStarted, meeting.state == .recording, stopWork == nil { scheduleStop() }
            }
        }
    }

    private func trigger(_ app: String) {
        // Not while a meeting is running or processing, or while fn is held. Try again next tick.
        guard meeting.state == .idle, isDictating?() != true, !prompting else { return }
        handled = true
        switch Mode(rawValue: Settings.autoDetectMeetings) ?? .ask {
        case .off: break
        case .auto:
            autoStarted = true
            meeting.toggle()
            notify?("Meeting detected in \(app): recording", 4)
        case .ask:
            prompting = true
            DispatchQueue.main.async { [weak self] in self?.ask(app) }
        }
    }

    private func ask(_ app: String) {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = "Looks like a meeting in \(app)"
        alert.informativeText = "Start recording and taking notes?"
        alert.addButton(withTitle: "Start")
        alert.addButton(withTitle: "Not now")
        let yes = alert.runModal() == .alertFirstButtonReturn
        prompting = false
        // The call may have ended while the alert was up, or the user started it by hand.
        if yes, meeting.state == .idle, isDictating?() != true {
            autoStarted = true
            meeting.toggle()
        }
    }

    private func scheduleStop() {
        let w = DispatchWorkItem { [weak self] in
            guard let self else { return }
            self.stopWork = nil
            // Someone may have rejoined a call during the grace period.
            guard self.activeSince == nil, self.autoStarted, self.meeting.state == .recording else { return }
            self.autoStarted = false
            self.meeting.toggle()
            self.notify?("Meeting ended: saving notes", 3)
        }
        stopWork = w
        DispatchQueue.main.asyncAfter(deadline: .now() + Self.stopGrace, execute: w)
    }

    // MARK: CoreAudio

    /// Name of a meeting app (other than us) that has an input stream running, if any.
    private func meetingAppUsingMic() -> String? {
        let me = ProcessInfo.processInfo.processIdentifier
        var addr = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyProcessObjectList,
                                              mScope: kAudioObjectPropertyScopeGlobal,
                                              mElement: kAudioObjectPropertyElementMain)
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size) == noErr,
              size > 0 else { return nil }
        var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
        guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &ids) == noErr
        else { return nil }
        for id in ids {
            guard let pid: pid_t = prop(id, kAudioProcessPropertyPID), pid != me else { continue }
            guard let running: UInt32 = prop(id, kAudioProcessPropertyIsRunningInput), running != 0 else { continue }
            guard let bundle = bundleID(id) else { continue }
            if let hit = Self.apps.first(where: { bundle.hasPrefix($0.prefix) }) { return hit.name }
        }
        return nil
    }

    private func prop<T>(_ obj: AudioObjectID, _ sel: AudioObjectPropertySelector) -> T? {
        var addr = AudioObjectPropertyAddress(mSelector: sel, mScope: kAudioObjectPropertyScopeGlobal,
                                              mElement: kAudioObjectPropertyElementMain)
        var size = UInt32(MemoryLayout<T>.size)
        let value = UnsafeMutablePointer<T>.allocate(capacity: 1)
        defer { value.deallocate() }
        guard AudioObjectGetPropertyData(obj, &addr, 0, nil, &size, value) == noErr else { return nil }
        return value.pointee
    }

    private func bundleID(_ obj: AudioObjectID) -> String? {
        var addr = AudioObjectPropertyAddress(mSelector: kAudioProcessPropertyBundleID,
                                              mScope: kAudioObjectPropertyScopeGlobal,
                                              mElement: kAudioObjectPropertyElementMain)
        var size = UInt32(MemoryLayout<CFString?>.size)
        var cf: CFString?
        let err = withUnsafeMutablePointer(to: &cf) {
            AudioObjectGetPropertyData(obj, &addr, 0, nil, &size, $0)
        }
        guard err == noErr, let s = cf else { return nil }
        return s as String
    }
}
