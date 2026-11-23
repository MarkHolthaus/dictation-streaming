import SwiftUI

/// Hub tab: past meetings on the left, transcript with speaker tagging on the right.
struct MeetingsTab: View {
    @StateObject private var model: MeetingsModel

    init(controller: MeetingController, request: @escaping ([String: Any]) async -> [String: Any]?) {
        _model = StateObject(wrappedValue: MeetingsModel(controller: controller, request: request))
    }

    var body: some View {
        HSplitView {
            MeetingListPane(model: model)
                .frame(minWidth: 220, idealWidth: 260, maxWidth: 340)
            MeetingDetailPane(model: model)
                .frame(minWidth: 420, maxWidth: .infinity)
        }
        .task { await model.loadList() }
    }
}

// MARK: - Left: list

private struct MeetingListPane: View {
    @ObservedObject var model: MeetingsModel

    var body: some View {
        VStack(spacing: 0) {
            LiveRow(model: model)
            Divider()
            if let e = model.listError {
                Banner(text: e, systemImage: "bolt.horizontal.circle", tint: .orange)
            }
            List(selection: Binding(get: { model.selected }, set: { model.select($0) })) {
                ForEach(model.meetings) { m in
                    MeetingRow(m: m).tag(m.stamp)
                }
            }
            .overlay {
                if model.meetings.isEmpty && model.listError == nil && !model.loadingList {
                    Text("No meetings yet").foregroundStyle(.secondary)
                }
            }
            Divider()
            HStack {
                Button { Task { await model.loadList() } } label: { Label("Refresh", systemImage: "arrow.clockwise") }
                    .buttonStyle(.borderless)
                if model.loadingList { ProgressView().controlSize(.small) }
                Spacer()
            }
            .padding(8)
        }
    }
}

private struct LiveRow: View {
    @ObservedObject var model: MeetingsModel

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { _ in
            let c = model.controller
            HStack(spacing: 8) {
                Circle().fill(c.state == .recording ? Color.red : Color.secondary).frame(width: 9, height: 9)
                VStack(alignment: .leading, spacing: 1) {
                    Text(c.active ? "Live now" : "No meeting running").font(.headline)
                    if c.active {
                        Text(c.state == .recording ? clock(Date().timeIntervalSince(c.startedAt)) : c.statusText)
                            .font(.caption).foregroundStyle(.secondary).monospacedDigit()
                    }
                }
                Spacer()
                Button(c.state == .recording ? "Stop" : "Start") { c.toggle() }
                    .disabled(c.state == .starting || c.state == .processing)
            }
            .padding(10)
        }
    }
}

private struct MeetingRow: View {
    let m: MeetingInfo

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text(m.title).lineLimit(2)
                Spacer(minLength: 4)
                if m.untagged > 0 {
                    Text("\(m.untagged)")
                        .font(.caption2.bold()).foregroundStyle(.white)
                        .padding(.horizontal, 6).padding(.vertical, 1)
                        .background(Capsule().fill(Color.orange))
                        .help("\(m.untagged) segments without a speaker name")
                }
            }
            Text(subtitle).font(.caption).foregroundStyle(.secondary)
        }
        .padding(.vertical, 2)
    }

    private var subtitle: String {
        let d = m.date.map { $0.formatted(date: .abbreviated, time: .shortened) } ?? m.stamp
        return "\(d) · \(m.durationS / 60) min"
    }
}

// MARK: - Right: detail

private struct MeetingDetailPane: View {
    @ObservedObject var model: MeetingsModel
    @State private var selection = Set<Int>()
    @State private var showAssign = false
    @State private var showRename = false
    @State private var showSummary = false

    var body: some View {
        if let info = model.selectedInfo {
            VStack(spacing: 0) {
                header(info)
                Divider()
                if let e = model.detailError { Banner(text: e, systemImage: "exclamationmark.triangle", tint: .orange) }
                if !model.summary.isEmpty { summaryBox }
                transcript
                Divider()
                footer
            }
            .onChange(of: model.selected) { _, _ in selection = [] }
        } else {
            Text("Pick a meeting").foregroundStyle(.secondary).frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private func header(_ info: MeetingInfo) -> some View {
        HStack {
            VStack(alignment: .leading) {
                Text(info.title).font(.title3.bold()).lineLimit(1)
                Text("\(model.segments.count) segments · \(model.untaggedNow) without a speaker")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Button("Open note") { model.openNote(info.notes.isEmpty ? info.transcript : info.notes) }
                .help("Opens in the default app for .md files")
            Button("Show in Finder") { model.reveal(info.notes.isEmpty ? info.transcript : info.notes) }
        }
        .padding(10)
    }

    private var summaryBox: some View {
        DisclosureGroup("Summary", isExpanded: $showSummary) {
            ScrollView {
                Text(markdown(model.summary)).textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxHeight: 220)
            Text("Retagging does not rewrite the summary.").font(.caption).foregroundStyle(.secondary)
        }
        .padding(10)
        .background(Color(nsColor: .controlBackgroundColor))
    }

    private var transcript: some View {
        List(selection: $selection) {
            ForEach(model.segments) { s in
                SegmentRow(model: model, s: s, selection: $selection).tag(s.i)
            }
        }
        .overlay {
            if model.loadingDetail { ProgressView() }
        }
        // Return opens the picker for the selected rows.
        .onKeyPress(.return) {
            guard !selection.isEmpty else { return .ignored }
            showAssign = true
            return .handled
        }
        .onKeyPress(.escape) {
            guard !selection.isEmpty else { return .ignored }
            selection = []
            return .handled
        }
    }

    private var footer: some View {
        HStack(spacing: 8) {
            Button { showAssign = true } label: { Label("Assign selected", systemImage: "person.crop.circle.badge.plus") }
                .disabled(selection.isEmpty)
                .popover(isPresented: $showAssign) {
                    SpeakerPicker(speakers: model.knownSpeakers, title: "Assign \(selection.count) row(s) to") { name in
                        model.assign(selection, to: name); showAssign = false
                    }
                }
            Button { showRename = true } label: { Label("Rename every…", systemImage: "arrow.left.arrow.right") }
                .disabled(model.labels.isEmpty)
                .popover(isPresented: $showRename) {
                    RenameAll(model: model) { showRename = false }
                }
            Spacer()
            if model.dirty {
                Text("\(model.pending.count) unsaved").foregroundStyle(.orange).font(.callout.bold())
                Button("Revert") { model.revert() }
                Button("Save") { Task { await model.save() } }
                    .buttonStyle(.borderedProminent).keyboardShortcut("s", modifiers: .command)
                    .disabled(model.saving)
            } else if model.justSaved {
                Label("Saved. The summary is not rewritten.", systemImage: "checkmark.circle")
                    .foregroundStyle(.green).font(.callout)
            } else {
                Text("Retagging changes speaker names in the transcript and note. The summary is not rewritten.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding(10)
    }

    private func markdown(_ s: String) -> AttributedString {
        let o = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        return (try? AttributedString(markdown: s, options: o)) ?? AttributedString(s)
    }
}

// MARK: - Transcript row

private struct SegmentRow: View {
    @ObservedObject var model: MeetingsModel
    let s: Segment
    @Binding var selection: Set<Int>
    @State private var showPicker = false

    private var name: String { model.speaker(of: s) }
    private var changed: Bool { model.pending[s.i] != nil }
    private var fromSystem: Bool { s.source == "system" }

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Image(systemName: fromSystem ? "speaker.wave.2" : "mic")
                .font(.caption).foregroundStyle(.secondary).frame(width: 14)
                .help(fromSystem ? "System audio (the other side)" : "Microphone (you)")
            Button { showPicker = true } label: { pill }
                .buttonStyle(.plain)
                .popover(isPresented: $showPicker) {
                    SpeakerPicker(speakers: model.knownSpeakers, title: "Speaker") { n in
                        // Clicking a row inside a multi-selection tags the whole selection.
                        model.assign(selection.contains(s.i) ? selection : [s.i], to: n)
                        showPicker = false
                    }
                }
            Text(clock(s.start)).font(.caption).monospacedDigit().foregroundStyle(.secondary)
            Text(s.text).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.vertical, 3)
        .padding(.leading, 6)
        .background(alignment: .leading) {
            Rectangle().fill(fromSystem ? Color.blue.opacity(0.7) : Color.green.opacity(0.7)).frame(width: 3)
        }
        .listRowBackground(fromSystem ? Color.blue.opacity(0.06) : nil)
    }

    private var pill: some View {
        HStack(spacing: 3) {
            if changed { Image(systemName: "pencil").font(.caption2) }
            Text(name.isEmpty ? "No speaker" : name).font(.caption.bold()).lineLimit(1)
        }
        .padding(.horizontal, 8).padding(.vertical, 2)
        .foregroundStyle(name.isEmpty ? Color.secondary : Color.primary)
        .background(Capsule().fill(name.isEmpty ? Color.gray.opacity(0.15) : pillColor(name).opacity(0.25)))
        .overlay(Capsule().strokeBorder(changed ? Color.orange : .clear, lineWidth: 1.5))
        .frame(minWidth: 70, alignment: .leading)
    }
}

private func pillColor(_ name: String) -> Color {
    let palette: [Color] = [.blue, .purple, .pink, .orange, .teal, .indigo, .mint, .brown]
    let h = name.unicodeScalars.reduce(0) { ($0 &* 31 &+ Int($1.value)) & 0xFFFF }
    return palette[h % palette.count]
}

// MARK: - Pickers

private struct SpeakerPicker: View {
    let speakers: [String]
    let title: String
    let pick: (String) -> Void
    @State private var typed = ""
    @FocusState private var focused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            ForEach(speakers, id: \.self) { n in
                Button(n) { pick(n) }.buttonStyle(.borderless)
            }
            Divider()
            TextField("New name", text: $typed)
                .focused($focused)
                .onSubmit { pick(typed) }
            Button("Add and assign") { pick(typed) }
                .disabled(typed.trimmingCharacters(in: .whitespaces).isEmpty)
        }
        .padding(12)
        .frame(minWidth: 190)
        .onAppear { focused = speakers.isEmpty }
    }
}

private struct RenameAll: View {
    @ObservedObject var model: MeetingsModel
    let done: () -> Void
    @State private var from = ""
    @State private var to = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Rename every…").font(.headline)
            Picker("Current name", selection: $from) {
                ForEach(model.labels, id: \.self) { Text($0).tag($0) }
            }
            TextField("New name", text: $to).onSubmit(apply)
            Menu("Pick from list") {
                ForEach(model.knownSpeakers, id: \.self) { n in Button(n) { to = n } }
            }
            HStack {
                Spacer()
                Button("Apply", action: apply).disabled(to.trimmingCharacters(in: .whitespaces).isEmpty || from.isEmpty)
            }
        }
        .padding(12)
        .frame(width: 260)
        .onAppear { from = model.labels.contains("Them") ? "Them" : (model.labels.first ?? "") }
    }

    private func apply() {
        guard !from.isEmpty, !to.trimmingCharacters(in: .whitespaces).isEmpty else { return }
        model.renameAll(from, to: to)
        done()
    }
}

private struct Banner: View {
    let text: String
    let systemImage: String
    let tint: Color

    var body: some View {
        Label(text, systemImage: systemImage)
            .font(.callout).foregroundStyle(tint)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(8)
            .background(tint.opacity(0.12))
    }
}

// MARK: - Preview

#if DEBUG
#Preview("Meetings") {
    MeetingsTab(controller: MeetingController()) { msg in
        switch msg["type"] as? String {
        case "meeting_list":
            return ["meetings": [
                ["stamp": "2026-11-02_1430", "title": "Design sync", "date": "2026-11-02T14:30:00",
                 "duration_s": 1800, "untagged": 3, "notes": "/tmp/n.md", "transcript": "/tmp/t.md"],
                ["stamp": "2026-10-30_0900", "title": "Study group", "date": "2026-10-30T09:00:00",
                 "duration_s": 900, "untagged": 0, "notes": "", "transcript": ""]]]
        case "meeting_get":
            return ["summary": "**Decisions**\n- Ship Friday", "segments": [
                ["i": 0, "start": 0.0, "end": 4.0, "source": "mic", "speaker": "Pat", "text": "Hey, can you hear me?"],
                ["i": 1, "start": 4.5, "end": 9.0, "source": "system", "speaker": "Them", "text": "Yes, loud and clear."],
                ["i": 2, "start": 10.0, "end": 12.0, "source": "system", "speaker": "", "text": "Let's start."]]]
        default:
            return ["ok": true, "error": ""]
        }
    }
    .frame(width: 900, height: 560)
}
#endif
