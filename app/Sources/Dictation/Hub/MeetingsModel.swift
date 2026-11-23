import AppKit
import SwiftUI

typealias EngineRequest = ([String: Any]) async -> [String: Any]?

struct MeetingInfo: Identifiable, Hashable {
    var id: String { stamp }
    let stamp: String
    let title: String
    let date: Date?
    let durationS: Int
    let untagged: Int
    let notes: String
    let transcript: String
}

struct Segment: Identifiable, Hashable {
    var id: Int { i }
    let i: Int
    let start: Double
    let end: Double
    let source: String // "mic" or "system"
    let speaker: String
    let text: String
}

private func int(_ v: Any?) -> Int { (v as? NSNumber)?.intValue ?? 0 }
private func double(_ v: Any?) -> Double { (v as? NSNumber)?.doubleValue ?? 0 }
private func string(_ v: Any?) -> String { v as? String ?? "" }

private func parseDate(_ s: String) -> Date? {
    let f = ISO8601DateFormatter()
    if let d = f.date(from: s) { return d }
    f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let d = f.date(from: s) { return d }
    // Naive local time such as 2026-11-02T14:30:00
    let l = DateFormatter()
    l.locale = Locale(identifier: "en_US_POSIX")
    l.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
    return l.date(from: String(s.prefix(19)))
}

/// Meetings tab state. Talks to the engine through `request`; a nil reply means it is not reachable.
@MainActor
final class MeetingsModel: ObservableObject {
    let controller: MeetingController
    private let request: EngineRequest

    @Published var meetings: [MeetingInfo] = []
    @Published var selected: String?
    @Published var segments: [Segment] = []
    @Published var summary = ""
    @Published var pending: [Int: String] = [:] // segment index -> new speaker
    @Published var listError: String?
    @Published var detailError: String?
    @Published var saving = false
    @Published var loadingList = false
    @Published var loadingDetail = false
    @Published var justSaved = false
    @Published var liveActive = false

    private var wasActive = false
    private var timer: Timer?

    init(controller: MeetingController, request: @escaping EngineRequest) {
        self.controller = controller
        self.request = request
        wasActive = controller.active
        liveActive = wasActive
        // MeetingController.onChange belongs to the app, so poll instead of replacing it.
        timer = Timer.scheduledTimer(withTimeInterval: 1.5, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.tick() }
        }
    }

    deinit { timer?.invalidate() }

    private func tick() {
        let now = controller.active
        if now != liveActive { liveActive = now }
        if wasActive && !now {
            // The engine writes the files just before it reports done.
            Task { try? await Task.sleep(nanoseconds: 1_500_000_000); await loadList() }
        }
        wasActive = now
    }

    var selectedInfo: MeetingInfo? { meetings.first { $0.stamp == selected } }
    var dirty: Bool { !pending.isEmpty }

    func speaker(of s: Segment) -> String { pending[s.i] ?? s.speaker }

    var untaggedNow: Int { segments.filter { speaker(of: $0).isEmpty }.count }

    /// Speakers to offer: the controller's list, then names already used in this meeting.
    var knownSpeakers: [String] {
        var out = controller.speakers
        for s in segments.map({ speaker(of: $0) }) where !s.isEmpty && !out.contains(s) { out.append(s) }
        return out
    }

    // MARK: loading

    func loadList() async {
        loadingList = true
        defer { loadingList = false }
        guard let r = await request(["type": "meeting_list"]) else {
            listError = "Engine not reachable. Start it and refresh."
            return
        }
        guard let raw = r["meetings"] as? [[String: Any]] else {
            listError = "The engine sent a meeting list I could not read."
            return
        }
        listError = nil
        meetings = raw.compactMap { d in
            let stamp = string(d["stamp"])
            guard !stamp.isEmpty else { return nil }
            let title = string(d["title"])
            return MeetingInfo(stamp: stamp, title: title.isEmpty ? stamp : title,
                               date: parseDate(string(d["date"])), durationS: int(d["duration_s"]),
                               untagged: int(d["untagged"]), notes: string(d["notes"]),
                               transcript: string(d["transcript"]))
        }.sorted { ($0.date ?? .distantPast, $0.stamp) > ($1.date ?? .distantPast, $1.stamp) }
        if selected == nil || !meetings.contains(where: { $0.stamp == selected }) {
            select(meetings.first?.stamp)
        }
    }

    func select(_ stamp: String?) {
        guard stamp != selected || segments.isEmpty else { return }
        selected = stamp
        segments = []; summary = ""; pending = [:]; detailError = nil; justSaved = false
        guard let stamp else { return }
        Task { await loadDetail(stamp) }
    }

    private func loadDetail(_ stamp: String) async {
        loadingDetail = true
        defer { loadingDetail = false }
        let r = await request(["type": "meeting_get", "stamp": stamp])
        guard stamp == selected else { return } // user moved on
        guard let r else { detailError = "Engine not reachable."; return }
        guard let raw = r["segments"] as? [[String: Any]] else {
            detailError = "The engine sent a transcript I could not read."
            return
        }
        detailError = nil
        summary = string(r["summary"])
        segments = raw.enumerated().map { n, d in
            Segment(i: d["i"] == nil ? n : int(d["i"]), start: double(d["start"]), end: double(d["end"]),
                    source: string(d["source"]), speaker: string(d["speaker"]), text: string(d["text"]))
        }
    }

    // MARK: tagging

    func assign(_ rows: Set<Int>, to name: String) {
        let n = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !n.isEmpty else { return }
        addToController(n)
        for s in segments where rows.contains(s.i) {
            if s.speaker == n { pending[s.i] = nil } else { pending[s.i] = n }
        }
        justSaved = false
    }

    /// "Rename every 'Them' to...": every row whose current label is `from`.
    func renameAll(_ from: String, to name: String) {
        let ids = Set(segments.filter { speaker(of: $0) == from }.map(\.i))
        assign(ids, to: name)
    }

    var labels: [String] {
        var seen: [String] = []
        for s in segments { let n = speaker(of: s); if !n.isEmpty && !seen.contains(n) { seen.append(n) } }
        return seen
    }

    func revert() { pending = [:]; justSaved = false }

    func save() async {
        guard let stamp = selected, dirty else { return }
        saving = true
        defer { saving = false }
        let assignments = pending.sorted { $0.key < $1.key }.map { ["i": $0.key, "speaker": $0.value] as [String: Any] }
        let r = await request(["type": "meeting_retag", "stamp": stamp, "assignments": assignments])
        guard let r else { detailError = "Engine not reachable. Nothing was saved."; return }
        guard (r["ok"] as? Bool) == true else {
            let e = string(r["error"])
            detailError = "Save failed: \(e.isEmpty ? "unknown error" : e)"
            return
        }
        detailError = nil
        segments = segments.map { s in
            guard let n = pending[s.i] else { return s }
            return Segment(i: s.i, start: s.start, end: s.end, source: s.source, speaker: n, text: s.text)
        }
        pending = [:]
        justSaved = true
        await loadList()
    }

    /// Reuses the controller's saved list. addSpeaker also makes the name the live speaker,
    /// so put the live speaker back.
    private func addToController(_ name: String) {
        guard !controller.speakers.contains(name) else { return }
        let live = controller.currentSpeaker
        controller.addSpeaker(name)
        if live != name { controller.setSpeaker(live) }
    }

    // MARK: files

    func openNote(_ path: String) {
        guard !path.isEmpty else { return }
        var c = URLComponents(string: "obsidian://open")
        c?.queryItems = [URLQueryItem(name: "path", value: path)]
        if let u = c?.url, NSWorkspace.shared.urlForApplication(toOpen: u) != nil {
            NSWorkspace.shared.open(u)
        } else {
            NSWorkspace.shared.open(URL(fileURLWithPath: path))
        }
    }

    func reveal(_ path: String) {
        guard !path.isEmpty else { return }
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
    }
}

func clock(_ seconds: Double) -> String {
    let s = Int(max(0, seconds))
    return s >= 3600 ? String(format: "%d:%02d:%02d", s / 3600, s / 60 % 60, s % 60)
                     : String(format: "%d:%02d", s / 60, s % 60)
}
