import AppKit
import CoreGraphics

enum Paster {
    /// Saves the pasteboard, sets `text`, posts Cmd-V, restores the old contents after about 400 ms.
    static func paste(_ text: String) {
        let pb = NSPasteboard.general
        let saved: [[(NSPasteboard.PasteboardType, Data)]] = (pb.pasteboardItems ?? []).map { item in
            item.types.compactMap { type in item.data(forType: type).map { (type, $0) } }
        }
        pb.clearContents()
        pb.setString(text, forType: .string)
        // nspasteboard.org marker: clipboard managers skip this short-lived entry.
        pb.setData(Data(), forType: NSPasteboard.PasteboardType("org.nspasteboard.TransientType"))
        let changeAfterSet = pb.changeCount

        postCmdV()

        DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) {
            guard pb.changeCount == changeAfterSet else { return } // user copied something meanwhile
            pb.clearContents()
            let items: [NSPasteboardItem] = saved.map { entries in
                let item = NSPasteboardItem()
                for (type, data) in entries { item.setData(data, forType: type) }
                return item
            }
            if !items.isEmpty { pb.writeObjects(items) }
        }
    }

    private static func postCmdV() {
        let src = CGEventSource(stateID: .hidSystemState)
        for down in [true, false] {
            guard let e = CGEvent(keyboardEventSource: src, virtualKey: 9, keyDown: down) else { continue }
            e.flags = .maskCommand
            e.setIntegerValueField(.eventSourceUserData, value: kSyntheticMagic)
            e.post(tap: .cghidEventTap)
        }
    }
}
