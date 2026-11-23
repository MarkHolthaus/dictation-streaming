import AppKit
import AVFoundation
import SwiftUI

extension Notification.Name {
    /// Posted by AppDelegate whenever the menu state refreshes (settings, meeting, engine).
    static let hubStateChanged = Notification.Name("hubStateChanged")
}

struct EngineStatus {
    var uptime = 0
    var asrBits = 8
    var models: [String: Bool] = [:]
    var counts: [String: Int] = [:]
    var errors: [(t: String, where_: String, msg: String)] = []

    init?(_ m: [String: Any]) {
        guard let up = m["uptime_s"] as? Int else { return nil }
        uptime = up
        asrBits = m["asr_bits"] as? Int ?? 8
        models = m["models"] as? [String: Bool] ?? [:]
        counts = m["counts"] as? [String: Int] ?? [:]
        for e in m["errors"] as? [[String: Any]] ?? [] {
            errors.append((e["t"] as? String ?? "", e["where"] as? String ?? "server", e["msg"] as? String ?? ""))
        }
    }
}

struct Problem: Identifiable {
    enum Level { case red, amber }
    let id: String
    let level: Level
    let text: String
    var fixTitle: String?
    var fixURL: String?
}

/// State behind the dashboard window: engine status polling, permission checks, problem list.
final class HubModel: ObservableObject {
    enum Load { case unknown, ok(EngineStatus), noReply }

    let coordinator: Coordinator
    @Published var load: Load = .unknown
    @Published var rev = 0 // bumped on any app-side change so views re-read Settings and permissions
    var onSettingChange: (() -> Void)?

    private var timer: Timer?
    private var observers: [NSObjectProtocol] = []

    init(coordinator: Coordinator) {
        self.coordinator = coordinator
        let nc = NotificationCenter.default
        for name in [Notification.Name.hubStateChanged, NSApplication.didBecomeActiveNotification] {
            observers.append(nc.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                self?.rev += 1
            })
        }
    }

    var engine: EngineClient { coordinator.engine }
    var meeting: MeetingController { coordinator.meeting }

    /// Polls `status` every 5 s while the window is visible.
    func setVisible(_ visible: Bool) {
        timer?.invalidate()
        timer = nil
        guard visible else { return }
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in self?.refresh() }
    }

    func refresh() {
        rev += 1
        guard engine.connected else { load = .unknown; return }
        Task { @MainActor in
            let reply = await engine.request(["type": "status"])
            if let reply, let s = EngineStatus(reply) { load = .ok(s) } else { load = .noReply }
        }
    }

    // MARK: settings (same effects as the menu)

    func setAutocorrect(_ on: Bool) {
        Settings.autocorrectEnabled = on
        coordinator.autocorrect.enabled = on
        coordinator.autocorrect.reset()
        changed()
    }
    func setLearn(_ on: Bool) {
        Settings.learnFromEdits = on
        if !on { coordinator.editLearner.discard() }
        changed()
    }
    func setWarm(_ on: Bool) { coordinator.setKeepWarm(on); changed() }
    func setDictation(_ on: Bool) { Settings.dictationEnabled = on; changed() }
    func setMode(_ m: String) { Settings.mode = m; changed() }
    func setAutoDetect(_ m: String) { Settings.autoDetectMeetings = m; changed() }
    private func changed() { onSettingChange?(); rev += 1 }

    // MARK: permissions and problems

    var micStatus: AVAuthorizationStatus { AVCaptureDevice.authorizationStatus(for: .audio) }

    var micText: String {
        switch micStatus {
        case .authorized: return Settings.keepMicWarm ? "Allowed, kept warm" : "Allowed, starts on key press"
        case .notDetermined: return "Not asked yet"
        default: return "Blocked"
        }
    }

    var problems: [Problem] {
        var out: [Problem] = []
        let pane = "x-apple.systempreferences:com.apple.preference.security?Privacy_"
        if !engine.connected {
            out.append(Problem(id: "engine", level: .red,
                               text: "The engine is not running, so nothing can be transcribed. Start it with ./run_engine.sh."))
        } else {
            switch load {
            case .noReply:
                out.append(Problem(id: "old", level: .amber,
                                   text: "The engine is connected but did not answer a status request. It may be an older version: restart it."))
            case .ok(let s):
                for (i, e) in s.errors.enumerated() {
                    out.append(Problem(id: "err\(i)", level: .amber, text: "\(e.where_): \(e.msg)"))
                }
            case .unknown: break
            }
        }
        if !AXIsProcessTrusted() {
            out.append(Problem(id: "ax", level: .red,
                               text: "Accessibility is off, so autocorrect and pasting cannot work.",
                               fixTitle: "Open Accessibility", fixURL: pane + "Accessibility"))
        }
        if !CGPreflightListenEventAccess() || !coordinator.tapInstalled {
            out.append(Problem(id: "input", level: .red,
                               text: "Input Monitoring is off or the keyboard tap is not running, so the fn key is not heard.",
                               fixTitle: "Open Input Monitoring", fixURL: pane + "ListenEvent"))
        }
        if micStatus != .authorized {
            out.append(Problem(id: "mic", level: .red,
                               text: "Microphone access is not allowed, so dictation hears nothing.",
                               fixTitle: "Open Microphone", fixURL: pane + "Microphone"))
        }
        if !CGPreflightScreenCaptureAccess() {
            out.append(Problem(id: "screen", level: .amber,
                               text: "Screen Recording is off, so meetings record only your mic, not the other side.",
                               fixTitle: "Open Screen Recording", fixURL: pane + "ScreenCapture"))
        }
        return out
    }

    var worstLevel: Problem.Level? {
        let p = problems
        if p.contains(where: { $0.level == .red }) { return .red }
        return p.isEmpty ? nil : .amber
    }
}
