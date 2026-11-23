import SwiftUI

enum HubTab: String, CaseIterable, Identifiable {
    case overview = "Overview", meetings = "Meetings", settings = "Settings", problems = "Problems"
    var id: String { rawValue }
    var icon: String {
        switch self {
        case .overview: return "gauge.with.dots.needle.bottom.50percent"
        case .meetings: return "person.2.wave.2"
        case .settings: return "gearshape"
        case .problems: return "exclamationmark.triangle"
        }
    }
}

extension Problem.Level {
    var color: Color { self == .red ? .red : .orange }
}

struct ProblemDot: View {
    let level: Problem.Level?
    var body: some View {
        if let level { Circle().fill(level.color).frame(width: 8, height: 8) }
    }
}

struct HubView: View {
    @ObservedObject var model: HubModel
    @State private var tab: HubTab? = .overview

    var body: some View {
        let _ = model.rev // re-evaluate on app-side changes
        let worst = model.worstLevel
        NavigationSplitView {
            List(HubTab.allCases, selection: $tab) { t in
                HStack {
                    Label(t.rawValue, systemImage: t.icon)
                    Spacer()
                    if t == .overview || t == .problems { ProblemDot(level: worst) }
                }
                .tag(t)
            }
            .navigationSplitViewColumnWidth(min: 150, ideal: 170, max: 220)
        } detail: {
            switch tab ?? .overview {
            case .overview: OverviewView(model: model)
            case .meetings:
                MeetingsTab(controller: model.meeting, request: { [engine = model.engine] in await engine.request($0) })
            case .settings: HubSettingsView(model: model)
            case .problems: ProblemsView(model: model)
            }
        }
        .frame(minWidth: 640, minHeight: 440)
    }
}
