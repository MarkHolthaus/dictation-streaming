import AppKit
import SwiftUI

struct ProblemsView: View {
    @ObservedObject var model: HubModel

    var body: some View {
        let _ = model.rev
        let items = model.problems
        VStack(alignment: .leading, spacing: 12) {
            Text("Problems").font(.title2.bold())
            if items.isEmpty {
                Label("Nothing wrong right now.", systemImage: "checkmark.circle")
                    .foregroundStyle(.green)
            } else {
                ForEach(items) { p in
                    HStack(alignment: .top, spacing: 10) {
                        Circle().fill(p.level.color).frame(width: 10, height: 10).padding(.top, 5)
                        Text(p.text).frame(maxWidth: .infinity, alignment: .leading)
                        if let title = p.fixTitle, let url = p.fixURL {
                            Button(title) { if let u = URL(string: url) { NSWorkspace.shared.open(u) } }
                        }
                    }
                    .padding(12)
                    .background(RoundedRectangle(cornerRadius: 10).fill(Color(nsColor: .controlBackgroundColor)))
                }
            }
            Spacer()
        }
        .padding(20)
    }
}
