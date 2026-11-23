import SwiftUI

struct HubSettingsView: View {
    @ObservedObject var model: HubModel
    @State private var newSpeaker = ""

    var body: some View {
        let _ = model.rev
        Form {
            Section("Dictation") {
                Toggle("Dictation (hold fn, right Option or right Command)", isOn: bind(Settings.dictationEnabled, model.setDictation))
                Toggle("Keep mic warm (no cut-off first words)", isOn: bind(Settings.keepMicWarm, model.setWarm))
                Picker("Mode", selection: bind(Settings.mode, model.setMode)) {
                    Text("Clean").tag("clean")
                    Text("Raw").tag("raw")
                }
                .pickerStyle(.segmented)
                Toggle("Learn from my edits", isOn: bind(Settings.learnFromEdits, model.setLearn))
            }
            Section("Typing") {
                Toggle("Autocorrect", isOn: bind(Settings.autocorrectEnabled, model.setAutocorrect))
            }
            Section("Meetings") {
                Picker("Auto-detect meetings", selection: bind(Settings.autoDetectMeetings, model.setAutoDetect)) {
                    Text("Off").tag("off")
                    Text("Ask").tag("ask")
                    Text("Auto").tag("auto")
                }
                .pickerStyle(.segmented)
                Text("Ask prompts when a call app is using the mic. Auto starts recording by itself and stops when the call ends.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section("Meeting speakers") {
                ForEach(model.meeting.speakers, id: \.self) { name in
                    HStack {
                        Text(name)
                        Spacer()
                        Button { model.meeting.removeSpeaker(name) } label: { Image(systemName: "minus.circle") }
                            .buttonStyle(.borderless)
                            .help("Remove \(name)")
                    }
                }
                HStack {
                    TextField("Add speaker", text: $newSpeaker).onSubmit(add)
                    Button("Add", action: add).disabled(newSpeaker.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
        }
        .formStyle(.grouped)
    }

    private func add() {
        model.meeting.addSpeaker(newSpeaker)
        newSpeaker = ""
    }

    private func bind<T>(_ value: T, _ set: @escaping (T) -> Void) -> Binding<T> {
        Binding(get: { value }, set: set)
    }
}
