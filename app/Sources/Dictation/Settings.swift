import Foundation

/// Marker written into eventSourceUserData of every event this app posts.
let kSyntheticMagic: Int64 = 0x4449_4354_4154_4531

enum Settings {
    private static let d = UserDefaults.standard

    static var dictationEnabled: Bool {
        get { d.object(forKey: "dictationEnabled") as? Bool ?? true }
        set { d.set(newValue, forKey: "dictationEnabled") }
    }
    static var autocorrectEnabled: Bool {
        get { d.object(forKey: "autocorrectEnabled") as? Bool ?? true }
        set { d.set(newValue, forKey: "autocorrectEnabled") }
    }
    /// "clean" or "raw"
    static var mode: String {
        get { d.string(forKey: "mode") ?? "clean" }
        set { d.set(newValue, forKey: "mode") }
    }
    /// Send `dictation_edit` feedback when the user edits pasted dictation.
    static var learnFromEdits: Bool {
        get { d.object(forKey: "learnFromEdits") as? Bool ?? true }
        set { d.set(newValue, forKey: "learnFromEdits") }
    }
    /// Keep the mic engine running so the first words are not cut off.
    static var keepMicWarm: Bool {
        get { d.object(forKey: "keepMicWarm") as? Bool ?? true }
        set { d.set(newValue, forKey: "keepMicWarm") }
    }
    /// "off", "ask" or "auto": what to do when a meeting is detected.
    static var autoDetectMeetings: String {
        get { d.string(forKey: "autoDetectMeetings") ?? "ask" }
        set { d.set(newValue, forKey: "autoDetectMeetings") }
    }
}
