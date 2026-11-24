import SwiftUI

struct FrictionView: View {
    @State private var summary: FrictionSummary?
    @State private var odoText = ""
    @State private var showAddDebt = false
    @State private var errorMsg: String?

    var body: some View {
        List {
            if let err = errorMsg {
                Section { Text(err).font(.caption).foregroundStyle(.red) }
            }

            if let s = summary {
                Section("Car · \(s.altis.vehicle)") {
                    LabeledContent("Fuel Spend", value: formatRM(s.altis.total_fuel_myr))
                    LabeledContent("Distance", value: "\(String(format: "%.0f", s.altis.distance_km)) km")
                    if let cpk = s.altis.cost_per_km_myr {
                        LabeledContent("Cost / km", value: formatRM(cpk))
                    }
                    HStack {
                        TextField("New Odometer (km)", text: $odoText).keyboardType(.decimalPad)
                        Button("Log") {
                            guard let km = Double(odoText), km >= 0 else { return }
                            Task {
                                try? await APIClient.shared.logOdometer(km: km)
                                odoText = ""
                                await load()
                            }
                        }.disabled(Double(odoText) == nil)
                    }
                }

                if !s.gear.isEmpty {
                    Section("Gear Consumables") {
                        ForEach(s.gear) { g in
                            HStack {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(g.name).font(.subheadline.weight(.medium))
                                    Text("\(Int(g.remaining_units))/\(Int(g.max_units)) \(g.unit) left")
                                        .font(.caption).foregroundStyle(g.alert ? Color.orange : Color.secondary)
                                }
                                Spacer()
                                Button("+1") {
                                    Task {
                                        try? await APIClient.shared.useGear(id: g.id)
                                        await load()
                                    }
                                }
                                .buttonStyle(.bordered)
                                Button("Reset") {
                                    Task {
                                        try? await APIClient.shared.resetGear(id: g.id)
                                        await load()
                                    }
                                }
                                .buttonStyle(.bordered)
                            }
                        }
                    }
                }

                Section("Debts · Owed \(formatRM(s.total_owed_myr)) · I Owe \(formatRM(s.total_i_owe_myr))") {
                    if s.unsettled_debts.isEmpty {
                        Text("No unsettled debts.").font(.caption).foregroundStyle(.secondary)
                    }
                    ForEach(s.unsettled_debts) { d in
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text("\(d.borrower_name) · \(d.event_name)")
                                    .font(.subheadline.weight(.medium))
                                Text(d.direction == "i_owe" ? "I owe" : "Owed to me")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Text(formatRM(d.remaining_myr))
                                .font(.subheadline.weight(.semibold).monospacedDigit())
                                .foregroundStyle(d.direction == "i_owe" ? .red : .green)
                            Button("Settle") {
                                Task {
                                    try? await APIClient.shared.settleDebt(id: d.id)
                                    await load()
                                }
                            }
                            .buttonStyle(.bordered)
                            .font(.caption)
                        }
                        .swipeActions {
                            Button(role: .destructive) {
                                Task {
                                    try? await APIClient.shared.deleteDebt(id: d.id)
                                    await load()
                                }
                            } label: { Label("Delete", systemImage: "trash") }
                        }
                    }
                }
            }
        }
        .navigationTitle("Car & Debts")
        .refreshable { await load() }
        .task { await load() }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { showAddDebt = true } label: { Image(systemName: "plus") }
            }
        }
        .sheet(isPresented: $showAddDebt) {
            AddDebtSheet { await load() }
        }
    }

    private func load() async {
        do { summary = try await APIClient.shared.fetchFrictionSummary() }
        catch { errorMsg = error.localizedDescription }
    }
}

private struct AddDebtSheet: View {
    @Environment(\.dismiss) private var dismiss
    var onSaved: () async -> Void
    @State private var eventName = ""
    @State private var person = ""
    @State private var amountText = ""
    @State private var direction = "owed_to_me"

    var body: some View {
        NavigationStack {
            Form {
                TextField("Person Name", text: $person)
                TextField("Event / Note", text: $eventName)
                TextField("Amount (MYR)", text: $amountText).keyboardType(.decimalPad)
                Picker("Direction", selection: $direction) {
                    Text("Owed to me").tag("owed_to_me")
                    Text("I owe").tag("i_owe")
                }
            }
            .navigationTitle("Add Debt")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        guard let amt = Double(amountText), amt > 0 else { return }
                        Task {
                            _ = try? await APIClient.shared.createDebt(
                                eventName: eventName.isEmpty ? "Personal" : eventName,
                                borrower: person, amount: amt, direction: direction
                            )
                            await onSaved()
                            dismiss()
                        }
                    }.disabled(person.isEmpty || (Double(amountText) ?? 0) <= 0)
                }
            }
        }
    }
}
