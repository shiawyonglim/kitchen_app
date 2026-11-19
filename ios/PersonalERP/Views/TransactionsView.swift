import SwiftUI

struct TransactionsView: View {
    @EnvironmentObject private var appState: AppState
    @State private var searchText = ""
    @State private var selectedFilter = "all"
    @State private var hideSelf = false
    @State private var editingEntry: LedgerEntry?
    @State private var showAddSheet = false

    private var filteredEntries: [LedgerEntry] {
        appState.entries.filter { entry in
            if hideSelf && entry.is_self_transfer && selectedFilter != "self" {
                return false
            }
            switch selectedFilter {
            case "expense":
                if entry.is_self_transfer || entry.tx_type != "expense" { return false }
            case "income":
                if entry.is_self_transfer || entry.tx_type != "income" { return false }
            case "repayment":
                if entry.is_self_transfer || entry.tx_type != "repayment" { return false }
            case "self":
                if !entry.is_self_transfer { return false }
            default:
                break
            }
            if !searchText.isEmpty {
                let q = searchText.lowercased()
                let hay = "\(entry.effectiveMerchant) \(entry.category) \(entry.account_name) \(entry.shortDate)".lowercased()
                if !hay.contains(q) { return false }
            }
            return true
        }
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                HStack(spacing: 8) {
                    Picker("Filter", selection: $selectedFilter) {
                        Text("All").tag("all")
                        Text("Exp").tag("expense")
                        Text("Inc").tag("income")
                        Text("Repay").tag("repayment")
                        Text("Self").tag("self")
                    }
                    .pickerStyle(.segmented)

                    Toggle("Hide Self", isOn: $hideSelf)
                        .toggleStyle(.button)
                        .font(.caption)
                }
                .padding(.horizontal)
                .padding(.vertical, 8)

                List(filteredEntries) { entry in
                    Button {
                        editingEntry = entry
                    } label: {
                        HStack(spacing: 10) {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(entry.effectiveMerchant)
                                    .font(.subheadline.weight(.medium))
                                    .foregroundStyle(.primary)
                                    .lineLimit(1)
                                Text("\(entry.shortDate) · \(entry.category) · \(entry.account_name)")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                            }
                            Spacer()
                            VStack(alignment: .trailing, spacing: 2) {
                                Text(amountLabel(for: entry))
                                    .font(.subheadline.semibold().monospacedDigit())
                                    .foregroundStyle(amountColor(for: entry))
                                if entry.is_self_transfer {
                                    Text("Self")
                                        .font(.caption2)
                                        .foregroundStyle(.secondary)
                                } else if entry.tx_type == "repayment" {
                                    Text("Repay")
                                        .font(.caption2)
                                        .foregroundStyle(.blue)
                                }
                            }
                        }
                    }
                }
                .listStyle(.plain)
                .refreshable {
                    await appState.loadAll()
                }
            }
            .searchable(text: $searchText, prompt: "Search merchant, category, account")
            .navigationTitle("Transactions")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showAddSheet = true
                    } label: {
                        Image(systemName: "plus")
                    }
                }
            }
            .sheet(item: $editingEntry) { entry in
                EditTransactionSheet(entry: entry)
                    .environmentObject(appState)
            }
            .sheet(isPresented: $showAddSheet) {
                AddTransactionSheet()
                    .environmentObject(appState)
            }
        }
    }

    private func amountLabel(for e: LedgerEntry) -> String {
        if e.is_self_transfer { return formatRM(e.amount_myr) }
        let sign = e.tx_type == "expense" ? "-" : "+"
        return "\(sign)\(formatRM(e.effectiveAmount))"
    }

    private func amountColor(for e: LedgerEntry) -> Color {
        if e.is_self_transfer { return .secondary }
        if e.tx_type == "income" { return .green }
        if e.tx_type == "repayment" { return .blue }
        return .red
    }
}

private struct EditTransactionSheet: View {
    @EnvironmentObject private var appState: AppState
    @Environment(\.dismiss) private var dismiss
    let entry: LedgerEntry
    @State private var selectedCategory: String

    init(entry: LedgerEntry) {
        self.entry = entry
        _selectedCategory = State(initialValue: entry.category)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Transaction") {
                    LabeledContent("Merchant", value: entry.effectiveMerchant)
                    LabeledContent("Date", value: entry.shortDate)
                    LabeledContent("Account", value: entry.account_name)
                    LabeledContent("Amount", value: formatRM(entry.effectiveAmount))
                    LabeledContent("Type", value: entry.tx_type.capitalized)
                }

                Section("Category") {
                    let options = Array(Set(appState.categories + [entry.category])).sorted()
                    Picker("Category", selection: $selectedCategory) {
                        ForEach(options, id: \.self) { cat in
                            Text(cat).tag(cat)
                        }
                    }
                    Button("Save Category") {
                        Task {
                            await appState.updateCategory(entryId: entry.id, category: selectedCategory)
                            dismiss()
                        }
                    }
                    .disabled(selectedCategory == entry.category)
                }

                Section {
                    Button("Toggle Income / Repayment / Expense") {
                        Task {
                            await appState.toggleType(entryId: entry.id)
                            dismiss()
                        }
                    }
                }
            }
            .navigationTitle("Edit Transaction")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }
}

private struct AddTransactionSheet: View {
    @EnvironmentObject private var appState: AppState
    @Environment(\.dismiss) private var dismiss
    @State private var merchant = ""
    @State private var amountText = ""
    @State private var txType = "expense"
    @State private var category = "Food & Beverage"
    @State private var account = "Touch 'n Go"
    @State private var remark = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("Details") {
                    TextField("Merchant", text: $merchant)
                    TextField("Amount (MYR)", text: $amountText)
                        .keyboardType(.decimalPad)
                    Picker("Type", selection: $txType) {
                        Text("Expense").tag("expense")
                        Text("Income").tag("income")
                        Text("Repayment").tag("repayment")
                    }
                    let cats = appState.categories.isEmpty ? ["Food & Beverage", "Transport", "General"] : appState.categories
                    Picker("Category", selection: $category) {
                        ForEach(cats, id: \.self) { Text($0).tag($0) }
                    }
                    TextField("Account", text: $account)
                    TextField("Remark (optional)", text: $remark)
                }
            }
            .navigationTitle("Add Transaction")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        guard let amt = Double(amountText), amt > 0 else { return }
                        Task {
                            if await appState.addManual(
                                merchant: merchant,
                                category: category,
                                txType: txType,
                                amount: amt,
                                account: account,
                                remark: remark
                            ) {
                                dismiss()
                            }
                        }
                    }
                    .disabled(merchant.isEmpty || (Double(amountText) ?? 0) <= 0)
                }
            }
        }
    }
}
