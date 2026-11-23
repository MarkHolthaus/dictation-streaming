import AppKit

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private let coordinator = Coordinator()
    private var statusItem: NSStatusItem!
    private let dictationItem = NSMenuItem()
    private let autocorrectItem = NSMenuItem()
    private let cleanItem = NSMenuItem()
    private let rawItem = NSMenuItem()
    private let warmItem = NSMenuItem()
    private let learnItem = NSMenuItem()
    private let statusLine = NSMenuItem()
    private let permLine = NSMenuItem()
    private let meetingItem = NSMenuItem()
    private let speakerItem = NSMenuItem()
    private let meetingStatus = NSMenuItem()
    private let detectItem = NSMenuItem()
    private var detectItems: [(String, NSMenuItem)] = []
    private lazy var hub: HubWindowController = {
        let h = HubWindowController(coordinator: coordinator)
        h.model.onSettingChange = { [weak self] in self?.refresh() }
        return h
    }()

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem.button {
            if let img = NSImage(systemSymbolName: "mic.fill", accessibilityDescription: "Dictation") {
                button.image = img
            } else {
                button.title = "Dict"
            }
        }

        let menu = NSMenu()
        menu.delegate = self
        menu.autoenablesItems = false

        dictationItem.title = "Dictation (hold fn, right Option or right Command)"
        dictationItem.action = #selector(toggleDictation)
        autocorrectItem.title = "Autocorrect"
        autocorrectItem.action = #selector(toggleAutocorrect)

        let modeItem = NSMenuItem(title: "Mode", action: nil, keyEquivalent: "")
        let modeMenu = NSMenu()
        cleanItem.title = "Clean"; cleanItem.action = #selector(setClean)
        rawItem.title = "Raw"; rawItem.action = #selector(setRaw)
        modeMenu.addItem(cleanItem); modeMenu.addItem(rawItem)
        modeItem.submenu = modeMenu

        warmItem.title = "Keep mic warm (no cut-off first words)"
        warmItem.action = #selector(toggleWarm)

        learnItem.title = "Learn from my edits"
        learnItem.action = #selector(toggleLearn)

        statusLine.isEnabled = false
        permLine.isEnabled = false

        for item in [dictationItem, autocorrectItem, cleanItem, rawItem, warmItem, learnItem] { item.target = self }

        menu.addItem(dictationItem)
        menu.addItem(autocorrectItem)
        menu.addItem(modeItem)
        menu.addItem(warmItem)
        menu.addItem(learnItem)
        menu.addItem(.separator())
        meetingItem.action = #selector(toggleMeeting); meetingItem.target = self
        speakerItem.title = "Who is talking"
        speakerItem.submenu = NSMenu()
        meetingStatus.isEnabled = false
        menu.addItem(meetingItem)
        menu.addItem(speakerItem)
        menu.addItem(meetingStatus)
        detectItem.title = "Auto-detect meetings"
        let detectMenu = NSMenu()
        for (key, title) in [("off", "Off"), ("ask", "Ask"), ("auto", "Auto")] {
            let it = NSMenuItem(title: title, action: #selector(pickDetect(_:)), keyEquivalent: "")
            it.target = self; it.representedObject = key
            detectMenu.addItem(it); detectItems.append((key, it))
        }
        detectItem.submenu = detectMenu
        menu.addItem(detectItem)
        menu.addItem(.separator())
        menu.addItem(statusLine)
        menu.addItem(permLine)
        menu.addItem(.separator())
        let dash = NSMenuItem(title: "Open Dashboard...", action: #selector(openDashboard), keyEquivalent: "d")
        dash.target = self
        menu.addItem(dash)
        let quit = NSMenuItem(title: "Quit", action: #selector(quit), keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)
        statusItem.menu = menu

        coordinator.onStateChange = { [weak self] in self?.refresh() }
        coordinator.start()
        refresh()
    }

    /// Opening the app again (Finder, Spotlight, a dock or menu-bar shortcut) shows the dashboard.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        hub.show()
        return true
    }

    func menuWillOpen(_ menu: NSMenu) { refresh() }

    private func refresh() {
        dictationItem.state = Settings.dictationEnabled ? .on : .off
        autocorrectItem.state = Settings.autocorrectEnabled ? .on : .off
        cleanItem.state = Settings.mode == "clean" ? .on : .off
        rawItem.state = Settings.mode == "raw" ? .on : .off
        warmItem.state = Settings.keepMicWarm ? .on : .off
        learnItem.state = Settings.learnFromEdits ? .on : .off
        let e = coordinator.engine
        statusLine.title = e.connected
            ? "Engine: connected" + (e.models.isEmpty ? "" : " (\(e.models))")
            : "Engine: not connected (retrying)"
        permLine.title = coordinator.tapInstalled
            ? "Keyboard access: OK"
            : "Keyboard access: needs Accessibility and Input Monitoring"
        statusItem.button?.alphaValue = e.connected ? 1.0 : 0.5
        for (key, it) in detectItems { it.state = Settings.autoDetectMeetings == key ? .on : .off }
        detectItem.title = "Auto-detect meetings: \(Settings.autoDetectMeetings.capitalized)"
        coordinator.detector.settingChanged()
        refreshMeeting()
        NotificationCenter.default.post(name: .hubStateChanged, object: nil)
    }

    private func refreshMeeting() {
        let m = coordinator.meeting
        switch m.state {
        case .idle: meetingItem.title = "Start meeting"
        case .starting: meetingItem.title = "Starting meeting..."
        case .recording:
            let s = Int(Date().timeIntervalSince(m.startedAt))
            meetingItem.title = String(format: "Stop meeting (%d:%02d)", s / 60, s % 60)
        case .processing: meetingItem.title = "Meeting: working..."
        }
        meetingItem.isEnabled = m.state == .idle || m.state == .recording
        meetingStatus.title = m.statusText
        meetingStatus.isHidden = m.statusText.isEmpty
        if let img = NSImage(systemSymbolName: m.state == .recording ? "record.circle.fill" : "mic.fill",
                             accessibilityDescription: "Dictation") {
            statusItem.button?.image = img
            statusItem.button?.contentTintColor = m.state == .recording ? .systemRed : nil
        }
        let sub = speakerItem.submenu ?? NSMenu()
        sub.removeAllItems()
        for name in m.speakers {
            let it = NSMenuItem(title: name, action: #selector(pickSpeaker(_:)), keyEquivalent: "")
            it.target = self
            it.state = name == m.currentSpeaker ? .on : .off
            sub.addItem(it)
        }
        sub.addItem(.separator())
        let add = NSMenuItem(title: "Add speaker...", action: #selector(addSpeaker), keyEquivalent: "")
        add.target = self
        sub.addItem(add)
        let rm = NSMenuItem(title: "Remove speaker", action: nil, keyEquivalent: "")
        let rmMenu = NSMenu()
        for name in m.speakers {
            let it = NSMenuItem(title: name, action: #selector(removeSpeaker(_:)), keyEquivalent: "")
            it.target = self
            rmMenu.addItem(it)
        }
        rm.submenu = rmMenu
        sub.addItem(rm)
        speakerItem.submenu = sub
        speakerItem.title = "Who is talking: \(m.currentSpeaker)"
    }

    @objc private func pickDetect(_ item: NSMenuItem) {
        Settings.autoDetectMeetings = item.representedObject as? String ?? "ask"
        refresh()
    }
    @objc private func toggleMeeting() { coordinator.meeting.toggle(); refresh() }
    @objc private func pickSpeaker(_ item: NSMenuItem) { coordinator.meeting.setSpeaker(item.title) }
    @objc private func removeSpeaker(_ item: NSMenuItem) { coordinator.meeting.removeSpeaker(item.title) }
    @objc private func addSpeaker() {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = "Add speaker"
        alert.informativeText = "Name of the person who is talking now."
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 220, height: 24))
        alert.accessoryView = field
        alert.addButton(withTitle: "Add")
        alert.addButton(withTitle: "Cancel")
        alert.window.initialFirstResponder = field
        if alert.runModal() == .alertFirstButtonReturn { coordinator.meeting.addSpeaker(field.stringValue) }
    }

    @objc private func toggleDictation() { Settings.dictationEnabled.toggle(); refresh() }
    @objc private func toggleAutocorrect() {
        Settings.autocorrectEnabled.toggle()
        coordinator.autocorrect.enabled = Settings.autocorrectEnabled
        coordinator.autocorrect.reset()
        refresh()
    }
    @objc private func setClean() { Settings.mode = "clean"; refresh() }
    @objc private func setRaw() { Settings.mode = "raw"; refresh() }
    @objc private func toggleWarm() { coordinator.setKeepWarm(!Settings.keepMicWarm); refresh() }
    @objc private func toggleLearn() {
        Settings.learnFromEdits.toggle()
        if !Settings.learnFromEdits { coordinator.editLearner.discard() }
        refresh()
    }
    @objc private func openDashboard() { hub.show() }
    @objc private func quit() { NSApp.terminate(nil) }
}
