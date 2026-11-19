import AppIntents
import SwiftUI
import WidgetKit

struct ERPWidgetConfigIntent: WidgetConfigurationIntent {
    static var title: LocalizedStringResource = "Wealth Widget Settings"
    static var description = IntentDescription("Paste your Server URL and Widget Token from the app's Settings tab.")

    @Parameter(title: "Server URL", default: "")
    var serverURL: String

    @Parameter(title: "Widget Token", default: "")
    var widgetToken: String
}

struct ERPEntry: TimelineEntry {
    let date: Date
    let summary: WidgetSummary?
    let errorText: String?
}

struct ERPProvider: AppIntentTimelineProvider {
    func placeholder(in context: Context) -> ERPEntry {
        ERPEntry(date: Date(), summary: sampleSummary, errorText: nil)
    }

    func snapshot(for configuration: ERPWidgetConfigIntent, in context: Context) async -> ERPEntry {
        if context.isPreview {
            return ERPEntry(date: Date(), summary: sampleSummary, errorText: nil)
        }
        do {
            let data = try await fetchWidgetSummary(config: configuration)
            return ERPEntry(date: Date(), summary: data, errorText: nil)
        } catch {
            return ERPEntry(date: Date(), summary: nil, errorText: error.localizedDescription)
        }
    }

    func timeline(for configuration: ERPWidgetConfigIntent, in context: Context) async -> Timeline<ERPEntry> {
        let nextRefresh = Calendar.current.date(byAdding: .minute, value: 15, to: Date()) ?? Date().addingTimeInterval(900)
        do {
            let data = try await fetchWidgetSummary(config: configuration)
            let entry = ERPEntry(date: Date(), summary: data, errorText: nil)
            return Timeline(entries: [entry], policy: .after(nextRefresh))
        } catch {
            let entry = ERPEntry(date: Date(), summary: nil, errorText: error.localizedDescription)
            return Timeline(entries: [entry], policy: .after(nextRefresh))
        }
    }

    private func resolveCreds(config: ERPWidgetConfigIntent) -> (baseURL: String, token: String)? {
        let cfgURL = config.serverURL.trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        let cfgTok = config.widgetToken.trimmingCharacters(in: .whitespacesAndNewlines)
        if !cfgURL.isEmpty && !cfgTok.isEmpty {
            return (cfgURL, cfgTok)
        }
        if let shared = SharedConfig.loadCredentials() {
            return (shared.baseURL, shared.token)
        }
        return nil
    }

    private func fetchWidgetSummary(config: ERPWidgetConfigIntent) async throws -> WidgetSummary {
        guard let creds = resolveCreds(config: config),
              let url = URL(string: "\(creds.baseURL)/api/wealth/widget-summary") else {
            throw NSError(
                domain: "PersonalERPWidget",
                code: 1,
                userInfo: [NSLocalizedDescriptionKey: "Hold widget -> Edit Widget -> paste Server URL & Token"]
            )
        }
        var req = URLRequest(url: url)
        req.timeoutInterval = 10
        req.setValue("Bearer \(creds.token)", forHTTPHeaderField: "Authorization")
        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw NSError(domain: "PersonalERPWidget", code: 2, userInfo: [NSLocalizedDescriptionKey: "Server offline or bad token"])
        }
        return try JSONDecoder().decode(WidgetSummary.self, from: data)
    }

    private var sampleSummary: WidgetSummary {
        WidgetSummary(
            month: "2026-11",
            today: "2026-11-19",
            today_expense_myr: 24.50,
            today_income_myr: 0,
            month_expense_myr: 1240.00,
            month_income_myr: 2900.00,
            monthly_budget_myr: 3000.00,
            budget_remaining_myr: 1760.00,
            budget_used_pct: 41.3,
            daily_burn_myr: 65.00,
            burn_pace_ratio: 0.85,
            remaining_fixed_bills_myr: 250.0,
            accounts: [],
            recent: [],
            updated_at: ""
        )
    }
}

struct PersonalERPWidgetEntryView: View {
    @Environment(\.widgetFamily) var family
    var entry: ERPEntry

    var body: some View {
        if let s = entry.summary {
            switch family {
            case .accessoryRectangular:
                VStack(alignment: .leading, spacing: 2) {
                    Text("Today -\(formatRM(s.today_expense_myr))")
                        .font(.caption.bold().monospacedDigit())
                    Text("Left \(formatRM(s.budget_remaining_myr))")
                        .font(.caption2.monospacedDigit())
                    Text("\(String(format: "%.0f", s.budget_used_pct))% of budget")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            case .systemMedium:
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        Text("Spend · \(s.month)")
                            .font(.caption.bold())
                            .foregroundStyle(.secondary)
                        Spacer()
                        Text("\(String(format: "%.1f", s.budget_used_pct))%")
                            .font(.caption.bold())
                            .foregroundStyle(s.budget_used_pct > 90 ? .red : Color(red: 0.51, green: 0.55, blue: 0.97))
                    }
                    HStack(alignment: .firstTextBaseline) {
                        Text("-\(formatRM(s.today_expense_myr))")
                            .font(.title2.bold().monospacedDigit())
                        Spacer()
                        Text("\(s.budget_remaining_myr >= 0 ? "Left" : "Over") \(formatRM(abs(s.budget_remaining_myr)))")
                            .font(.caption.weight(.semibold).monospacedDigit())
                            .foregroundStyle(s.budget_remaining_myr >= 0 ? .green : .red)
                    }
                    ForEach(s.recent.prefix(2)) { tx in
                        HStack {
                            Text(tx.merchant)
                                .font(.caption2)
                                .lineLimit(1)
                            Spacer()
                            let sign = tx.tx_type == "expense" ? "-" : "+"
                            Text("\(sign)\(formatRM(tx.amount_myr))")
                                .font(.caption2.monospacedDigit())
                                .foregroundStyle(tx.tx_type == "expense" ? .red : .green)
                        }
                    }
                }
                .padding(2)
            default:
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        Text(s.month)
                            .font(.caption2.bold())
                            .foregroundStyle(.secondary)
                        Spacer()
                        Text("\(String(format: "%.0f", s.budget_used_pct))%")
                            .font(.caption2.bold())
                            .foregroundStyle(s.budget_used_pct > 90 ? .red : Color(red: 0.51, green: 0.55, blue: 0.97))
                    }
                    Spacer()
                    Text("Today")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    Text("-\(formatRM(s.today_expense_myr))")
                        .font(.headline.bold().monospacedDigit())
                    Text("\(s.budget_remaining_myr >= 0 ? "Left" : "Over") \(formatRM(abs(s.budget_remaining_myr)))")
                        .font(.caption2.weight(.semibold).monospacedDigit())
                        .foregroundStyle(s.budget_remaining_myr >= 0 ? .green : .red)
                }
            }
        } else {
            VStack(alignment: .leading, spacing: 4) {
                Text("Personal ERP")
                    .font(.caption.bold())
                Text(entry.errorText ?? "Hold -> Edit Widget")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

@main
struct PersonalERPWidget: Widget {
    let kind: String = "PersonalERPWidget"

    var body: some WidgetConfiguration {
        AppIntentConfiguration(kind: kind, intent: ERPWidgetConfigIntent.self, provider: ERPProvider()) { entry in
            PersonalERPWidgetEntryView(entry: entry)
                .containerBackground(Color(red: 0.067, green: 0.071, blue: 0.086), for: .widget)
        }
        .configurationDisplayName("Wealth Summary")
        .description("Today's spend, monthly budget left, and recent transactions.")
        .supportedFamilies([.systemSmall, .systemMedium, .accessoryRectangular])
    }
}
