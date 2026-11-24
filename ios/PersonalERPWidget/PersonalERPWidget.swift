import SwiftUI
import WidgetKit

struct ERPEntry: TimelineEntry {
    let date: Date
    let summary: WidgetSummary?
    let errorText: String?
}

struct ERPProvider: TimelineProvider {
    func placeholder(in context: Context) -> ERPEntry {
        ERPEntry(date: Date(), summary: sampleSummary, errorText: nil)
    }

    func getSnapshot(in context: Context, completion: @escaping (ERPEntry) -> Void) {
        completion(ERPEntry(date: Date(), summary: sampleSummary, errorText: nil))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<ERPEntry>) -> Void) {
        Task {
            let nextRefresh = Calendar.current.date(byAdding: .minute, value: 15, to: Date()) ?? Date().addingTimeInterval(900)
            do {
                let data = try await fetchWidgetSummary()
                let entry = ERPEntry(date: Date(), summary: data, errorText: nil)
                completion(Timeline(entries: [entry], policy: .after(nextRefresh)))
            } catch {
                let entry = ERPEntry(date: Date(), summary: nil, errorText: error.localizedDescription)
                completion(Timeline(entries: [entry], policy: .after(nextRefresh)))
            }
        }
    }

    private func resolveCreds() -> (baseURL: String, token: String)? {
        if let shared = SharedConfig.loadCredentials() {
            return (shared.baseURL, shared.token)
        }
        if let plistURL = Bundle.main.object(forInfoDictionaryKey: "ERPWidgetBaseURL") as? String,
           let plistTok = Bundle.main.object(forInfoDictionaryKey: "ERPWidgetToken") as? String,
           !plistURL.isEmpty, !plistTok.isEmpty {
            return (plistURL.trimmingCharacters(in: CharacterSet(charactersIn: "/")), plistTok)
        }
        return nil
    }

    private func fetchWidgetSummary() async throws -> WidgetSummary {
        guard let creds = resolveCreds(),
              let url = URL(string: "\(creds.baseURL)/api/wealth/widget-summary") else {
            throw NSError(
                domain: "PersonalERPWidget",
                code: 1,
                userInfo: [NSLocalizedDescriptionKey: "Missing widget credentials"]
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
            accounts: [
                WidgetAccountBalance(name: "Touch 'n Go", balance_myr: 420.50),
                WidgetAccountBalance(name: "Ryt Bank", balance_myr: 1850.00),
                WidgetAccountBalance(name: "CIMB Bank", balance_myr: 610.00)
            ],
            recent: [
                WidgetRecentTx(merchant: "Mixue", category: "Food & Beverage", tx_type: "expense", amount_myr: 6.50, account_name: "Touch 'n Go", occurred_at: "2026-11-22"),
                WidgetRecentTx(merchant: "Shell", category: "Fuel/Toll/parking", tx_type: "expense", amount_myr: 40.00, account_name: "Ryt Bank", occurred_at: "2026-11-22")
            ],
            unpaid_bills: [
                WidgetBillItem(id: 1, name: "Phone Installment", amount_myr: 180.00, due_day: 25),
                WidgetBillItem(id: 2, name: "Google One", amount_myr: 8.49, due_day: 28)
            ],
            assistant_pending_count: 2,
            assistant_tasks: [
                WidgetAssistantTask(id: 1, title: "Pay phone installment", category: "task", priority: "high", scheduled_for: "Today"),
                WidgetAssistantTask(id: 2, title: "Check groceries", category: "reminder", priority: "normal", scheduled_for: "")
            ],
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
                Text(entry.errorText ?? "Unavailable")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

struct PersonalERPWidget: Widget {
    let kind: String = "PersonalERPWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: ERPProvider()) { entry in
            PersonalERPWidgetEntryView(entry: entry)
                .containerBackground(Color(red: 0.067, green: 0.071, blue: 0.086), for: .widget)
        }
        .configurationDisplayName("Wealth Summary")
        .description("Today's spend, monthly budget left, and recent transactions.")
        .supportedFamilies([.systemSmall, .systemMedium, .accessoryRectangular])
    }
}

struct AssistantWidgetEntryView: View {
    @Environment(\.widgetFamily) var family
    var entry: ERPEntry

    var body: some View {
        if let s = entry.summary {
            let count = s.assistant_pending_count ?? 0
            let tasks = s.assistant_tasks ?? []
            switch family {
            case .accessoryRectangular:
                VStack(alignment: .leading, spacing: 2) {
                    Text("AI Tasks (\(count))")
                        .font(.caption.bold())
                    Text(tasks.first?.title ?? "All caught up")
                        .font(.caption2)
                        .lineLimit(2)
                }
            default:
                let maxItems = family == .systemMedium ? 3 : 2
                VStack(alignment: .leading, spacing: 5) {
                    HStack(spacing: 4) {
                        Image(systemName: "sparkles")
                            .font(.caption2.bold())
                            .foregroundStyle(Color(red: 0.51, green: 0.55, blue: 0.97))
                        Text("AI Assistant")
                            .font(.caption2.bold())
                            .foregroundStyle(.secondary)
                        Spacer()
                        Text("\(count)")
                            .font(.caption2.bold().monospacedDigit())
                            .foregroundStyle(count > 0 ? Color(red: 0.51, green: 0.55, blue: 0.97) : .green)
                    }
                    if tasks.isEmpty {
                        Spacer()
                        Text("All caught up!")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(.green)
                        Spacer()
                    } else {
                        ForEach(tasks.prefix(maxItems)) { t in
                            HStack(spacing: 4) {
                                Circle()
                                    .fill(t.priority == "high" ? Color.red : Color(red: 0.51, green: 0.55, blue: 0.97))
                                    .frame(width: 5, height: 5)
                                Text(t.title)
                                    .font(.caption2.weight(.medium))
                                    .lineLimit(1)
                                Spacer()
                                if family == .systemMedium && !t.scheduled_for.isEmpty {
                                    Text(t.scheduled_for)
                                        .font(.caption2)
                                        .foregroundStyle(.secondary)
                                }
                            }
                        }
                        Spacer(minLength: 0)
                    }
                }
            }
        } else {
            VStack(alignment: .leading, spacing: 4) {
                Text("AI Assistant")
                    .font(.caption.bold())
                Text(entry.errorText ?? "Unavailable")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

struct AssistantWidget: Widget {
    let kind: String = "AssistantWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: ERPProvider()) { entry in
            AssistantWidgetEntryView(entry: entry)
                .containerBackground(Color(red: 0.067, green: 0.071, blue: 0.086), for: .widget)
        }
        .configurationDisplayName("AI Assistant")
        .description("Pending AI action items and reminders.")
        .supportedFamilies([.systemSmall, .systemMedium, .accessoryRectangular])
    }
}

struct BankBalancesWidgetEntryView: View {
    @Environment(\.widgetFamily) var family
    var entry: ERPEntry

    var body: some View {
        if let s = entry.summary {
            let total = s.accounts.reduce(0.0) { $0 + $1.balance_myr }
            switch family {
            case .accessoryRectangular:
                VStack(alignment: .leading, spacing: 2) {
                    Text("Banks \(formatRM(total))")
                        .font(.caption.bold().monospacedDigit())
                    ForEach(s.accounts.prefix(2)) { a in
                        Text("\(a.name): \(formatRM(a.balance_myr))")
                            .font(.caption2.monospacedDigit())
                            .lineLimit(1)
                    }
                }
            default:
                let maxRows = family == .systemMedium ? 4 : 3
                VStack(alignment: .leading, spacing: 5) {
                    HStack {
                        Text("Banks")
                            .font(.caption2.bold())
                            .foregroundStyle(.secondary)
                        Spacer()
                        Text(formatRM(total))
                            .font(.caption.bold().monospacedDigit())
                            .foregroundStyle(total >= 0 ? .green : .red)
                    }
                    if s.accounts.isEmpty {
                        Spacer()
                        Text("No accounts")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                        Spacer()
                    } else {
                        ForEach(s.accounts.prefix(maxRows)) { a in
                            HStack {
                                Text(a.name)
                                    .font(.caption2.weight(.medium))
                                    .lineLimit(1)
                                Spacer()
                                Text(formatRM(a.balance_myr))
                                    .font(.caption2.weight(.semibold).monospacedDigit())
                                    .foregroundStyle(a.balance_myr >= 0 ? Color.primary : Color.red)
                            }
                        }
                        Spacer(minLength: 0)
                    }
                }
            }
        } else {
            VStack(alignment: .leading, spacing: 4) {
                Text("Banks")
                    .font(.caption.bold())
                Text(entry.errorText ?? "Unavailable")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

struct BankBalancesWidget: Widget {
    let kind: String = "BankBalancesWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: ERPProvider()) { entry in
            BankBalancesWidgetEntryView(entry: entry)
                .containerBackground(Color(red: 0.067, green: 0.071, blue: 0.086), for: .widget)
        }
        .configurationDisplayName("Bank Balances")
        .description("Money left in each bank and e-wallet.")
        .supportedFamilies([.systemSmall, .systemMedium, .accessoryRectangular])
    }
}

struct CashFlowWidgetEntryView: View {
    @Environment(\.widgetFamily) var family
    var entry: ERPEntry

    var body: some View {
        if let s = entry.summary {
            let net = s.month_income_myr - s.month_expense_myr
            switch family {
            case .accessoryRectangular:
                VStack(alignment: .leading, spacing: 2) {
                    Text("Net \(net >= 0 ? "+" : "-")\(formatRM(abs(net)))")
                        .font(.caption.bold().monospacedDigit())
                    Text("In +\(formatRM(s.month_income_myr))")
                        .font(.caption2.monospacedDigit())
                    Text("Burn \(formatRM(s.daily_burn_myr))/d")
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
            default:
                VStack(alignment: .leading, spacing: 5) {
                    HStack {
                        Text("Cash Flow · \(s.month)")
                            .font(.caption2.bold())
                            .foregroundStyle(.secondary)
                        Spacer()
                        Text("\(formatRM(s.daily_burn_myr))/d")
                            .font(.caption2.monospacedDigit())
                            .foregroundStyle(.secondary)
                    }
                    HStack {
                        Text("In")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                        Spacer()
                        Text("+\(formatRM(s.month_income_myr))")
                            .font(.caption2.weight(.semibold).monospacedDigit())
                            .foregroundStyle(.green)
                    }
                    HStack {
                        Text("Out")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                        Spacer()
                        Text("-\(formatRM(s.month_expense_myr))")
                            .font(.caption2.weight(.semibold).monospacedDigit())
                            .foregroundStyle(.red)
                    }
                    HStack {
                        Text("Net")
                            .font(.caption2.bold())
                        Spacer()
                        Text("\(net >= 0 ? "+" : "-")\(formatRM(abs(net)))")
                            .font(.caption.bold().monospacedDigit())
                            .foregroundStyle(net >= 0 ? .green : .red)
                    }
                    if family == .systemMedium {
                        HStack {
                            Text("Fixed Bills Left")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                            Spacer()
                            Text(formatRM(s.remaining_fixed_bills_myr))
                                .font(.caption2.monospacedDigit())
                                .foregroundStyle(.orange)
                        }
                    }
                }
            }
        } else {
            VStack(alignment: .leading, spacing: 4) {
                Text("Cash Flow")
                    .font(.caption.bold())
                Text(entry.errorText ?? "Unavailable")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

struct CashFlowWidget: Widget {
    let kind: String = "CashFlowWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: ERPProvider()) { entry in
            CashFlowWidgetEntryView(entry: entry)
                .containerBackground(Color(red: 0.067, green: 0.071, blue: 0.086), for: .widget)
        }
        .configurationDisplayName("Cash Flow")
        .description("Monthly income, expenses, net flow, and daily burn.")
        .supportedFamilies([.systemSmall, .systemMedium, .accessoryRectangular])
    }
}

struct RecentTxWidgetEntryView: View {
    @Environment(\.widgetFamily) var family
    var entry: ERPEntry

    var body: some View {
        if let s = entry.summary {
            switch family {
            case .accessoryRectangular:
                VStack(alignment: .leading, spacing: 2) {
                    Text("Latest")
                        .font(.caption.bold())
                    if let tx = s.recent.first {
                        let sign = tx.tx_type == "expense" ? "-" : "+"
                        Text(tx.merchant)
                            .font(.caption2)
                            .lineLimit(1)
                        Text("\(sign)\(formatRM(tx.amount_myr))")
                            .font(.caption2.monospacedDigit())
                    } else {
                        Text("No transactions")
                            .font(.caption2)
                    }
                }
            default:
                let maxRows = family == .systemMedium ? 4 : 3
                VStack(alignment: .leading, spacing: 5) {
                    HStack {
                        Text("Recent")
                            .font(.caption2.bold())
                            .foregroundStyle(.secondary)
                        Spacer()
                        Text(s.month)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                    if s.recent.isEmpty {
                        Spacer()
                        Text("No recent activity")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                        Spacer()
                    } else {
                        ForEach(s.recent.prefix(maxRows)) { tx in
                            HStack(spacing: 4) {
                                Text(tx.merchant)
                                    .font(.caption2.weight(.medium))
                                    .lineLimit(1)
                                Spacer()
                                let sign = tx.tx_type == "expense" ? "-" : "+"
                                Text("\(sign)\(formatRM(tx.amount_myr))")
                                    .font(.caption2.weight(.semibold).monospacedDigit())
                                    .foregroundStyle(tx.tx_type == "expense" ? .red : .green)
                            }
                        }
                        Spacer(minLength: 0)
                    }
                }
            }
        } else {
            VStack(alignment: .leading, spacing: 4) {
                Text("Recent")
                    .font(.caption.bold())
                Text(entry.errorText ?? "Unavailable")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

struct RecentTxWidget: Widget {
    let kind: String = "RecentTxWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: ERPProvider()) { entry in
            RecentTxWidgetEntryView(entry: entry)
                .containerBackground(Color(red: 0.067, green: 0.071, blue: 0.086), for: .widget)
        }
        .configurationDisplayName("Recent Transactions")
        .description("Latest card and e-wallet transactions.")
        .supportedFamilies([.systemSmall, .systemMedium, .accessoryRectangular])
    }
}

struct BillsWidgetEntryView: View {
    @Environment(\.widgetFamily) var family
    var entry: ERPEntry

    var body: some View {
        if let s = entry.summary {
            let bills = s.unpaid_bills ?? []
            switch family {
            case .accessoryRectangular:
                VStack(alignment: .leading, spacing: 2) {
                    Text("Bills Left \(formatRM(s.remaining_fixed_bills_myr))")
                        .font(.caption.bold().monospacedDigit())
                    if let b = bills.first {
                        Text("Day \(b.due_day): \(b.name)")
                            .font(.caption2)
                            .lineLimit(1)
                    } else {
                        Text("All paid!")
                            .font(.caption2)
                    }
                }
            default:
                let maxRows = family == .systemMedium ? 4 : 3
                VStack(alignment: .leading, spacing: 5) {
                    HStack {
                        Text("Unpaid Bills")
                            .font(.caption2.bold())
                            .foregroundStyle(.secondary)
                        Spacer()
                        Text(formatRM(s.remaining_fixed_bills_myr))
                            .font(.caption.bold().monospacedDigit())
                            .foregroundStyle(bills.isEmpty ? .green : .orange)
                    }
                    if bills.isEmpty {
                        Spacer()
                        Text("All bills paid!")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(.green)
                        Spacer()
                    } else {
                        ForEach(bills.prefix(maxRows)) { b in
                            HStack(spacing: 4) {
                                Text("D\(b.due_day)")
                                    .font(.caption2.monospacedDigit())
                                    .foregroundStyle(.secondary)
                                Text(b.name)
                                    .font(.caption2.weight(.medium))
                                    .lineLimit(1)
                                Spacer()
                                Text(formatRM(b.amount_myr))
                                    .font(.caption2.weight(.semibold).monospacedDigit())
                                    .foregroundStyle(.orange)
                            }
                        }
                        Spacer(minLength: 0)
                    }
                }
            }
        } else {
            VStack(alignment: .leading, spacing: 4) {
                Text("Unpaid Bills")
                    .font(.caption.bold())
                Text(entry.errorText ?? "Unavailable")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

struct BillsWidget: Widget {
    let kind: String = "BillsWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: ERPProvider()) { entry in
            BillsWidgetEntryView(entry: entry)
                .containerBackground(Color(red: 0.067, green: 0.071, blue: 0.086), for: .widget)
        }
        .configurationDisplayName("Upcoming Bills")
        .description("Unpaid fixed bills and due days for the month.")
        .supportedFamilies([.systemSmall, .systemMedium, .accessoryRectangular])
    }
}

@main
struct PersonalERPWidgets: WidgetBundle {
    var body: some Widget {
        PersonalERPWidget()
        BankBalancesWidget()
        CashFlowWidget()
        RecentTxWidget()
        BillsWidget()
        AssistantWidget()
    }
}
