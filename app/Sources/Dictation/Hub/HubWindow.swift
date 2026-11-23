import AppKit
import SwiftUI

/// One reusable dashboard window. Closing it only hides it.
final class HubWindowController: NSObject, NSWindowDelegate {
    let model: HubModel
    private var window: NSWindow?

    init(coordinator: Coordinator) {
        model = HubModel(coordinator: coordinator)
        super.init()
    }

    func show() {
        if window == nil {
            let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 760, height: 520),
                             styleMask: [.titled, .closable, .miniaturizable, .resizable],
                             backing: .buffered, defer: false)
            w.title = "Dictation"
            w.isReleasedWhenClosed = false
            w.contentView = NSHostingView(rootView: HubView(model: model))
            w.setFrameAutosaveName("DictationHub")
            w.delegate = self
            w.center()
            window = w
            NotificationCenter.default.addObserver(self, selector: #selector(visibilityChanged),
                                                   name: NSWindow.didChangeOcclusionStateNotification, object: w)
        }
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
        visibilityChanged()
    }

    func windowWillClose(_ notification: Notification) { model.setVisible(false) }
    func windowDidMiniaturize(_ notification: Notification) { model.setVisible(false) }
    func windowDidDeminiaturize(_ notification: Notification) { visibilityChanged() }

    @objc private func visibilityChanged() {
        guard let w = window else { return }
        model.setVisible(w.isVisible && w.occlusionState.contains(.visible))
    }
}
