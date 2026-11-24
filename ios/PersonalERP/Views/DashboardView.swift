import SwiftUI

struct DashboardView: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        NavigationStack {
            List {
                if let status = appState.statusMessage {
                    Section {
                        Text(status)
                            .font(.caption)
                            .foregroundStyle(.green)
                    }
                }
                if let err = appState.errorMessage {
                    Section {
                        Text(err)
                            .font(.caption)
                            .foregroundStyle(.red)
                    }
                }

                if let wid = appState.widgetSummary {
                    Section("Today & Monthly Budget (\(wid.month))") {
                        HStack {
                            VStack(alignment: .leading, spacing: 4) {
                                Text("Today's Spend")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                Text("-\(formatRM(wid.today_expense_myr))")
                                    .font(.title2.bold().monospacedDigit())
                                    .foregroundStyle(.red)
                            }
                            Spacer()
                            VStack(alignment: .trailing, spacing: 4) {
                                Text(wid.budget_remaining_myr >= 0 ? "Budget Left" : "Over Budget")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                Text(formatRM(abs(wid.budget_remaining_myr)))
                                    .font(.title3.weight(.semibold).monospacedDigit())
                                    .foregroundStyle(wid.budget_remaining_myr >= 0 ? .green : .red)
                            }
                        }

                        VStack(alignment: .leading, spacing: 6) {
                            ProgressView(value: min(max(wid.budget_used_pct, 0), 100), total: 100)
                                .tint(wid.budget_used_pct > 90 ? .red : Color(red: 0.51, green: 0.55, blue: 0.97))
                            HStack {
                                Text("Spent \(formatRM(wid.month_expense_myr))")
                                Spacer()
                                Text("\(String(format: "%.1f", wid.budget_used_pct))% of \(formatRM(wid.monthly_budget_myr))")
                            }
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        }
                    }
                }

                if let sum = appState.summary {
                    Section("Cash Flow Summary") {
                        LabeledContent("Income") {
                            Text("+\(formatRM(sum.total_income_myr))")
                                .foregroundStyle(.green)
                                .monospacedDigit()
                        }
                        LabeledContent("Repayments") {
                            Text("+\(formatRM(sum.total_repayment_myr))")
                                .foregroundStyle(.blue)
                                .monospacedDigit()
                        }
                        LabeledContent("Expenses") {
                            Text("-\(formatRM(sum.total_expense_myr))")
                                .foregroundStyle(.red)
                                .monospacedDigit()
                        }
                        LabeledContent("Net Cash Flow") {
                            Text(formatRM(sum.net_cash_flow_myr))
                                .fontWeight(.semibold)
                                .foregroundStyle(sum.net_cash_flow_myr >= 0 ? .green : .red)
                                .monospacedDigit()
                        }
                        LabeledContent("30d Daily Burn") {
                            Text("\(formatRM(sum.rolling_30d_daily_burn_myr))/day")
                                .monospacedDigit()
                        }
                    }

                    if !sum.account_balances.isEmpty {
                        Section("Accounts") {
                            ForEach(sum.account_balances, id: \.stableId) { acct in
                                HStack {
                                    VStack(alignment: .leading) {
                                        Text(acct.name)
                                            .fontWeight(.medium)
                                        Text("\(acct.tx_count) transactions")
                                            .font(.caption2)
                                            .foregroundStyle(.secondary)
                                    }
                                    Spacer()
                                    Text(formatRM(acct.current_balance_myr))
                                        .fontWeight(.semibold)
                                        .monospacedDigit()
                                }
                            }
                        }
                    }

                    let topCats = sum.category_breakdown.sorted { $0.value > $1.value }
                    if !topCats.isEmpty {
                        let totalCatSpend = max(1.0, sum.total_expense_myr)
                        Section("Spending by Category") {
                            ForEach(Array(topCats.prefix(8)), id: \.key) { cat, amount in
                                let pct = min(100.0, (amount / totalCatSpend) * 100.0)
                                NavigationLink {
                                    CategoryDetailView(category: cat)
                                        .environmentObject(appState)
                                } label: {
                                    VStack(alignment: .leading, spacing: 4) {
                                        HStack {
                                            Text(cat)
                                                .fontWeight(.medium)
                                            Spacer()
                                            Text("\(formatRM(amount)) · \(String(format: "%.0f", pct))%")
                                                .font(.caption)
                                                .monospacedDigit()
                                                .foregroundStyle(.secondary)
                                        }
                                        ProgressView(value: pct, total: 100)
                                            .tint(Color(red: 0.51, green: 0.55, blue: 0.97))
                                    }
                                    .padding(.vertical, 2)
                                }
                            }
                        }
                    }
                } else if appState.isLoading {
                    ProgressView("Loading wealth summary...")
                }
            }
            .navigationTitle("Wealth")
            .refreshable {
                await appState.loadAll()
            }
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    MonthMenuPicker()
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        Task { await appState.syncNow() }
                    } label: {
                        Label("Sync", systemImage: "arrow.triangle.2.circlepath")
                    }
                    .disabled(appState.isLoading)
                }
            }
        }
    }
}

private struct CategoryDetailView: View {
    @EnvironmentObject private var appState: AppState
    let category: String
    @State private var editingEntry: LedgerEntry?

    private var matching: [LedgerEntry] {
        appState.entries
            .filter { $0.category == category && !$0.is_self_transfer && $0.tx_type == "expense" }
            .sorted { $0.effectiveAmount > $1.effectiveAmount }
    }

    var body: some View {
        List(matching) { entry in
            Button {
                editingEntry = entry
            } label: {
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(entry.effectiveMerchant)
                            .font(.subheadline.weight(.medium))
                            .foregroundStyle(.primary)
                            .lineLimit(1)
                        Text("\(entry.shortDate) · \(entry.account_name)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Text("-\(formatRM(entry.effectiveAmount))")
                        .font(.subheadline.weight(.semibold).monospacedDigit())
                        .foregroundStyle(.red)
                }
            }
        }
        .navigationTitle(category)
        .sheet(item: $editingEntry) { entry in
            EditTransactionSheet(entry: entry)
                .environmentObject(appState)
        }
    }
}
