import AppKit
import CoreGraphics

/// One session-level event tap for flagsChanged, keyDown and mouse clicks. Runs on the main run loop.
final class EventTapController {
    var onFlagsChanged: ((CGEvent) -> Void)?
    /// Return true to swallow the key.
    var onKeyDown: ((CGEvent) -> Bool)?
    var onMouseDown: (() -> Void)?

    private var tap: CFMachPort?

    var isInstalled: Bool { tap != nil }

    @discardableResult
    func install() -> Bool {
        if tap != nil { return true }
        let types: [CGEventType] = [.flagsChanged, .keyDown, .leftMouseDown, .rightMouseDown, .otherMouseDown]
        var mask: CGEventMask = 0
        for t in types { mask |= CGEventMask(1) << CGEventMask(t.rawValue) }
        let ref = Unmanaged.passUnretained(self).toOpaque()
        guard let t = CGEvent.tapCreate(tap: .cgSessionEventTap, place: .headInsertEventTap,
                                        options: .defaultTap, eventsOfInterest: mask,
                                        callback: eventTapCallback, userInfo: ref) else { return false }
        tap = t
        let source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, t, 0)
        CFRunLoopAddSource(CFRunLoopGetMain(), source, .commonModes)
        CGEvent.tapEnable(tap: t, enable: true)
        return true
    }

    fileprivate func handle(_ type: CGEventType, _ event: CGEvent) -> Unmanaged<CGEvent>? {
        let pass = Unmanaged.passUnretained(event)
        if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
            if let tap { CGEvent.tapEnable(tap: tap, enable: true) }
            return pass
        }
        if event.getIntegerValueField(.eventSourceUserData) == kSyntheticMagic { return pass }
        switch type {
        case .flagsChanged:
            onFlagsChanged?(event)
        case .keyDown:
            if onKeyDown?(event) == true { return nil }
        case .leftMouseDown, .rightMouseDown, .otherMouseDown:
            onMouseDown?()
        default:
            break
        }
        return pass
    }
}

private func eventTapCallback(proxy: CGEventTapProxy, type: CGEventType, event: CGEvent,
                              refcon: UnsafeMutableRawPointer?) -> Unmanaged<CGEvent>? {
    guard let refcon else { return Unmanaged.passUnretained(event) }
    let controller = Unmanaged<EventTapController>.fromOpaque(refcon).takeUnretainedValue()
    return MainActor.assumeIsolated { controller.handle(type, event) }
}
