import SwiftUI

struct OverviewView: View {
    @ObservedObject var model: HubModel

    var body: some View {
        let _ = model.rev
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                HStack {
                    Text("Overview").font(.title2.bold())
                    ProblemDot(level: model.worstLevel)
                }
                card("Engine") {
                    row("Connection", model.engine.connected ? "Connected" : "Not connected")
                    if case .ok(let s) = model.load {
                        row("Uptime", uptime(s.uptime))
                        row("Speech model", s.asrBits == 0 ? "bf16" : "\(s.asrBits)-bit")
                        row("Loaded", modelList(s.models))
                        row("Dictations", "\(s.counts["dictations"] ?? 0)")
                        row("Typo fixes", "\(s.counts["fixes"] ?? 0)")
                        row("Meetings", "\(s.counts["meetings"] ?? 0)")
                    } else if model.engine.connected {
                        row("Status", "No answer yet")
                    }
                }
                card("This Mac") {
                    row("Microphone", model.micText)
                    row("Dictation", Settings.dictationEnabled ? "On" : "Off")
                    row("Autocorrect", Settings.autocorrectEnabled ? "On" : "Off")
                    row("Mode", Settings.mode == "raw" ? "Raw" : "Clean")
                    row("Auto-detect", Settings.autoDetectMeetings.capitalized)
                }
                MeetingCard(model: model)
            }
            .padding(20)
        }
    }

    private func modelList(_ m: [String: Bool]) -> String {
        let order = ["asr", "tagger", "context", "editor"]
        return order.map { k in
            let on = m[k] ?? false
            // The editor loads on demand, so "not loaded" is normal for it.
            return "\(k) \(on ? "✓" : (k == "editor" ? "on demand" : "✗"))"
        }.joined(separator: "   ")
    }

    private func uptime(_ s: Int) -> String {
        s >= 3600 ? "\(s / 3600) h \(s % 3600 / 60) min" : s >= 60 ? "\(s / 60) min" : "\(s) s"
    }
}

func card<C: View>(_ title: String, @ViewBuilder _ content: () -> C) -> some View {
    VStack(alignment: .leading, spacing: 8) {
        Text(title).font(.headline)
        content()
    }
    .frame(maxWidth: .infinity, alignment: .leading)
    .padding(14)
    .background(RoundedRectangle(cornerRadius: 10).fill(Color(nsColor: .controlBackgroundColor)))
    .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color(nsColor: .separatorColor)))
}

func row(_ label: String, _ value: String) -> some View {
    HStack {
        Text(label).foregroundStyle(.secondary)
        Spacer()
        Text(value).monospacedDigit()
    }
}

struct MeetingCard: View {
    @ObservedObject var model: HubModel

    var body: some View {
        let m = model.meeting
        card("Meeting") {
            switch m.state {
            case .idle: row("State", "Not recording")
            case .starting: row("State", "Starting...")
            case .recording:
                TimelineView(.periodic(from: .now, by: 1)) { ctx in
                    let s = max(0, Int(ctx.date.timeIntervalSince(m.startedAt)))
                    row("Recording", String(format: "%d:%02d", s / 60, s % 60))
                }
                row("Speaker", m.currentSpeaker)
            case .processing: row("State", m.statusText.isEmpty ? "Working..." : m.statusText)
            }
            if m.state == .idle || m.state == .recording {
                Button(m.state == .idle ? "Start meeting" : "Stop meeting") { m.toggle() }
                    .disabled(m.state == .idle && !model.engine.connected)
            }
        }
    }
}
