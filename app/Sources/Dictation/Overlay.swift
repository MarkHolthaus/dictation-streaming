import AppKit

private final class NoFocusPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

/// Small floating pill near the bottom center. Never takes focus, ignores the mouse.
final class Overlay {
    private let panel: NSPanel
    private let dot = NSView()
    private let label = NSTextField(labelWithString: "")
    private var hideWork: DispatchWorkItem?

    init() {
        let size = NSSize(width: 460, height: 40)
        panel = NoFocusPanel(contentRect: NSRect(origin: .zero, size: size),
                             styleMask: [.borderless, .nonactivatingPanel],
                             backing: .buffered, defer: false)
        panel.level = .statusBar
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.ignoresMouseEvents = true
        panel.hidesOnDeactivate = false
        panel.isFloatingPanel = true
        panel.becomesKeyOnlyIfNeeded = true
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]

        let blur = NSVisualEffectView(frame: NSRect(origin: .zero, size: size))
        blur.material = .hudWindow
        blur.blendingMode = .behindWindow
        blur.state = .active
        blur.wantsLayer = true
        blur.layer?.cornerRadius = size.height / 2
        blur.layer?.masksToBounds = true

        dot.frame = NSRect(x: 16, y: (size.height - 12) / 2, width: 12, height: 12)
        dot.wantsLayer = true
        dot.layer?.cornerRadius = 6
        dot.layer?.backgroundColor = NSColor.systemRed.cgColor

        label.frame = NSRect(x: 38, y: (size.height - 20) / 2, width: size.width - 54, height: 20)
        label.font = .systemFont(ofSize: 14, weight: .medium)
        label.textColor = .labelColor
        label.lineBreakMode = .byTruncatingHead
        label.maximumNumberOfLines = 1

        blur.addSubview(dot)
        blur.addSubview(label)
        panel.contentView = blur
    }

    func show(text: String, color: NSColor = .systemRed) {
        hideWork?.cancel()
        dot.layer?.backgroundColor = color.cgColor
        label.stringValue = text
        position()
        panel.orderFrontRegardless()
    }

    func setText(_ text: String) {
        label.stringValue = text.isEmpty ? "Listening..." : text
    }

    func hide(after delay: TimeInterval = 0) {
        hideWork?.cancel()
        let work = DispatchWorkItem { [weak self] in self?.panel.orderOut(nil) }
        hideWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: work)
    }

    private func position() {
        guard let screen = NSScreen.main else { return }
        let f = screen.visibleFrame
        let size = panel.frame.size
        panel.setFrameOrigin(NSPoint(x: f.midX - size.width / 2, y: f.minY + 70))
    }
}
