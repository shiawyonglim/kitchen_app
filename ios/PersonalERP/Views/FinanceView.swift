import SwiftUI

struct FinanceView: View {
    @EnvironmentObject private var appState: AppState
    @State private var overview: FinanceOverview?
    @State private var coachAdvice: String?
    @State private var coachModel: String?
    @State private var isLoading = false
    @State private var isCoaching = false
    @State private var errorMsg: String?

    var body: some View {
        List {
            if let err = errorMsg {
                Section { Text(err).font(.caption).foregroundStyle(.red) }
            }

            if let ov = overview {
                Section("Pulse · \(ov.month)") {
                    LabeledContent("Safe to Spend / Day") {
                        Text(formatRM(ov.safe_to_spend_daily_myr))
                            .fontWeight(.bold).monospacedDigit()
                            .foregroundStyle(ov.safe_to_spend_daily_myr > 0 ? .green : .red)
                    }
                    LabeledContent("Savings Rate") {
                        Text("\(String(format: "%.1f", ov.savings_rate_pct))% (\(ov.status))")
                            .monospacedDigit()
                    }
                    LabeledContent("Emergency Runway") {
                        Text("\(String(format: "%.1f", ov.emergency_months)) months")
                            .monospacedDigit()
                    }
                }

                Section("50 / 30 / 20 Breakdown") {
                    BucketRow(title: "Needs", detail: ov.buckets.needs)
                    BucketRow(title: "Wants", detail: ov.buckets.wants)
                    BucketRow(title: "Savings & Debt", detail: ov.buckets.savings_debt)
                }

                if !ov.leaks.isEmpty {
                    Section("Spending Leaks") {
                        ForEach(ov.leaks) { leak in
                            VStack(alignment: .leading, spacing: 3) {
                                HStack {
                                    Text(leak.title).font(.subheadline.weight(.medium))
                                    Spacer()
                                    Text(formatRM(leak.amount_myr))
                                        .font(.subheadline.weight(.semibold).monospacedDigit())
                                        .foregroundStyle(.orange)
                                }
                                Text(leak.tip)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            } else if isLoading {
                ProgressView("Loading finance overview...")
            }

            Section("AI Finance Coach") {
                Button {
                    Task { await askCoach() }
                } label: {
                    HStack {
                        Image(systemName: "sparkles")
                        Text(isCoaching ? "Analyzing..." : "Ask AI Finance Coach")
                    }
                }
                .disabled(isCoaching)

                if let advice = coachAdvice {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(advice).font(.caption)
                        if let m = coachModel {
                            Text("Model: \(m)").font(.caption2).foregroundStyle(.secondary)
                        }
                    }
                }
            }
        }
        .navigationTitle("Finance & Coach")
        .refreshable { await load() }
        .task { await load() }
    }

    private func load() async {
        isLoading = true; errorMsg = nil
        defer { isLoading = false }
        do {
            overview = try await APIClient.shared.fetchFinanceOverview(month: appState.selectedMonth)
        } catch {
            errorMsg = error.localizedDescription
        }
    }

    private func askCoach() async {
        isCoaching = true; errorMsg = nil
        defer { isCoaching = false }
        do {
            let res = try await APIClient.shared.fetchFinanceCoach(month: overview?.month ?? appState.selectedMonth)
            coachAdvice = res.advice
            coachModel = res.model
        } catch {
            errorMsg = error.localizedDescription
        }
    }
}

private struct BucketRow: View {
    let title: String
    let detail: BucketDetail

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(title).fontWeight(.medium)
                Spacer()
                Text("\(formatRM(detail.actual_myr)) · \(String(format: "%.0f", detail.actual_pct))% / \(detail.target_pct)%")
                    .font(.caption).monospacedDigit().foregroundStyle(.secondary)
            }
            ProgressView(value: min(max(detail.actual_pct, 0), 100), total: 100)
                .tint(detail.actual_pct > Double(detail.target_pct) ? .orange : Color(red: 0.51, green: 0.55, blue: 0.97))
        }
        .padding(.vertical, 2)
    }
}
