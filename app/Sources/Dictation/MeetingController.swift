import AppKit

/// Meeting start/stop, the speaker list and the engine's meeting messages.
/// Independent of hold-fn dictation: own recorder, own protocol messages.
final class MeetingController {
    enum State { case idle, starting, recording, processing }

    private let recorder = MeetingRecorder()
    private(set) var state: State = .idle
    private(set) var statusText = ""
    private(set) var startedAt = Date()
    private(set) var systemOn = false
    private(set) var currentSpeaker: String
    private(set) var speakers: [String]

    var send: (([String: Any]) -> Bool)?
    var connected: (() -> Bool)?
    var notify: ((String, TimeInterval) -> Void)?
    var onChange: (() -> Void)?

    init() {
        let saved = UserDefaults.standard.stringArray(forKey: "meetingSpeakers") ?? []
        speakers = saved.isEmpty ? ["Them"] : saved
        currentSpeaker = speakers[0]
        recorder.onChunk = { [weak self] source, data in
            guard let self, self.state == .recording || self.state == .processing else { return }
            _ = self.send?(["type": "meeting_audio", "source": source, "pcm16": data.base64EncodedString()])
        }
    }

    var active: Bool { state != .idle }

    func toggle() {
        switch state {
        case .idle: start()
        case .recording: stop()
        default: break
        }
    }

    private func start() {
        guard connected?() == true else { notify?("Engine not connected", 2.5); return }
        state = .starting
        statusText = "Starting..."
        onChange?()
        Task { [weak self] in
            guard let self else { return }
            let sys = await self.recorder.startSystemAudio()
            await MainActor.run {
                self.systemOn = sys
                guard self.send?(["type": "meeting_start", "system": sys]) == true else {
                    Task { await self.recorder.stop() }
                    self.state = .idle; self.statusText = ""; self.onChange?()
                    self.notify?("Engine not connected", 2.5)
                    return
                }
                do {
                    try self.recorder.startMic()
                } catch {
                    Task { await self.recorder.stop() }
                    _ = self.send?(["type": "meeting_stop"])
                    self.state = .idle; self.statusText = ""; self.onChange?()
                    self.notify?("Mic error: \(error.localizedDescription)", 3)
                    return
                }
                self.startedAt = Date()
                self.state = .recording
                self.statusText = sys ? "Recording (mic + system audio)" : "Recording (mic only)"
                _ = self.send?(["type": "meeting_speaker", "name": self.currentSpeaker])
                self.notify?(sys ? "Meeting recording" : "Meeting recording (mic only: allow Screen Recording for the other side)", 3)
                self.onChange?()
            }
        }
    }

    private func stop() {
        state = .processing
        statusText = "Transcribing..."
        onChange?()
        Task { [weak self] in
            guard let self else { return }
            await self.recorder.stop() // flushes the tails onto the main queue
            DispatchQueue.main.async { _ = self.send?(["type": "meeting_stop"]) }
        }
    }

    func setSpeaker(_ name: String) {
        currentSpeaker = name
        if state == .recording { _ = send?(["type": "meeting_speaker", "name": name]) }
        onChange?()
    }

    func addSpeaker(_ name: String) {
        let n = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !n.isEmpty else { return }
        if !speakers.contains(n) {
            speakers.append(n)
            UserDefaults.standard.set(speakers, forKey: "meetingSpeakers")
        }
        setSpeaker(n)
    }

    func removeSpeaker(_ name: String) {
        speakers.removeAll { $0 == name }
        if speakers.isEmpty { speakers = ["Them"] }
        UserDefaults.standard.set(speakers, forKey: "meetingSpeakers")
        if currentSpeaker == name { setSpeaker(speakers[0]) }
    }

    func handle(_ msg: [String: Any]) {
        switch msg["type"] as? String {
        case "meeting_status":
            switch msg["state"] as? String {
            case "transcribing": statusText = "Transcribing..."
            case "summarizing": statusText = "Writing notes (can take a few minutes)..."
            default: break
            }
            onChange?()
        case "meeting_done":
            state = .idle
            statusText = ""
            onChange?()
            let notes = msg["notes"] as? String ?? ""
            let transcript = msg["transcript"] as? String ?? ""
            if (msg["ok"] as? Bool) == true, !notes.isEmpty {
                notify?("Meeting notes saved", 4)
                NSWorkspace.shared.open(URL(fileURLWithPath: notes))
            } else if !transcript.isEmpty {
                notify?("Transcript saved, summary failed: \(msg["error"] as? String ?? "")", 6)
                NSWorkspace.shared.open(URL(fileURLWithPath: notes.isEmpty ? transcript : notes))
            } else {
                notify?("Meeting: \(msg["error"] as? String ?? "nothing saved")", 4)
            }
        default:
            break
        }
    }
}
