import SwiftUI

struct AssetsView: View {
    @State private var assets: [TrackedAsset] = []
    @State private var netWorth: NetWorthSummary?
    @State private var isLoading = false
    @State private var showAdd = false
    @State private var errorMsg: String?

    var body: some View {
        List {
            if let err = errorMsg {
                Section { Text(err).font(.caption).foregroundStyle(.red) }
            }

            if let nw = netWorth {
                Section("Net Worth") {
                    LabeledContent("Total Net Worth") {
                        Text(formatRM(nw.total_net_worth_myr))
                            .font(.headline.bold()).monospacedDigit().foregroundStyle(.green)
                    }
                    LabeledContent("Liquid Cash") {
                        Text(formatRM(nw.liquid_cash_myr)).monospacedDigit()
                    }
                    LabeledContent("Tracked Assets (\(nw.asset_count))") {
                        Text(formatRM(nw.tracked_assets_value_myr)).monospacedDigit()
                    }
                }
            }

            Section("Tracked Hardware & Collectibles") {
                if assets.isEmpty && !isLoading {
                    Text("No assets tracked yet.").font(.caption).foregroundStyle(.secondary)
                }
                ForEach(assets) { a in
                    HStack {
                        VStack(alignment: .leading, spacing: 3) {
                            Text("\(a.name) ×\(a.quantity)").font(.subheadline.weight(.medium))
                            Text("\(a.category) · \(a.condition) · Paid \(formatRM(a.purchase_price_myr))")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        VStack(alignment: .trailing, spacing: 2) {
                            Text(formatRM(a.total_liquid_value_myr))
                                .font(.subheadline.weight(.semibold).monospacedDigit())
                            Text("\(a.unrealized_pnl_myr >= 0 ? "+" : "")\(formatRM(a.unrealized_pnl_myr))")
                                .font(.caption2.monospacedDigit())
                                .foregroundStyle(a.unrealized_pnl_myr >= 0 ? .green : .red)
                        }
                    }
                    .swipeActions {
                        Button(role: .destructive) {
                            Task {
                                try? await APIClient.shared.deleteAsset(id: a.id)
                                await load()
                            }
                        } label: { Label("Delete", systemImage: "trash") }
                    }
                }
            }
        }
        .navigationTitle("Assets")
        .refreshable { await load() }
        .task { await load() }
        .toolbar {
            ToolbarItemGroup(placement: .topBarTrailing) {
                Button {
                    Task { await refreshPrices() }
                } label: { Image(systemName: "arrow.triangle.2.circlepath") }
                .disabled(isLoading)
                Button { showAdd = true } label: { Image(systemName: "plus") }
            }
        }
        .sheet(isPresented: $showAdd) {
            AddAssetSheet { await load() }
        }
    }

    private func load() async {
        isLoading = true; errorMsg = nil
        defer { isLoading = false }
        do {
            async let a = APIClient.shared.fetchAssets()
            async let nw = APIClient.shared.fetchNetWorth()
            (assets, netWorth) = try await (a, nw)
        } catch {
            errorMsg = error.localizedDescription
        }
    }

    private func refreshPrices() async {
        isLoading = true; errorMsg = nil
        defer { isLoading = false }
        do {
            assets = try await APIClient.shared.refreshAllAssets()
            netWorth = try await APIClient.shared.fetchNetWorth()
        } catch {
            errorMsg = error.localizedDescription
        }
    }
}

private struct AddAssetSheet: View {
    @Environment(\.dismiss) private var dismiss
    var onSaved: () async -> Void
    @State private var name = ""
    @State private var category = "Hardware"
    @State private var priceText = ""
    @State private var qty = 1

    var body: some View {
        NavigationStack {
            Form {
                TextField("Asset Name", text: $name)
                TextField("Category", text: $category)
                TextField("Purchase Price (MYR)", text: $priceText).keyboardType(.decimalPad)
                Stepper("Quantity: \(qty)", value: $qty, in: 1...50)
            }
            .navigationTitle("Track Asset")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        Task {
                            _ = try? await APIClient.shared.addAsset(
                                name: name, category: category,
                                purchasePrice: Double(priceText) ?? 0, quantity: qty
                            )
                            await onSaved()
                            dismiss()
                        }
                    }.disabled(name.isEmpty)
                }
            }
        }
    }
}
