import SwiftUI

struct KitchenView: View {
    @State private var items: [KitchenItem] = []
    @State private var shopping: [ShoppingListItem] = []
    @State private var recipe: DinnerRecipe?
    @State private var newShopText = ""
    @State private var showAdd = false
    @State private var isLoading = false
    @State private var errorMsg: String?

    var body: some View {
        List {
            if let err = errorMsg {
                Section { Text(err).font(.caption).foregroundStyle(.red) }
            }

            Section("AI Chef") {
                Button {
                    Task { await makeRecipe() }
                } label: {
                    Label(isLoading ? "Generating..." : "Suggest Dinner from Expiring Items", systemImage: "fork.knife")
                }
                .disabled(isLoading)

                if let r = recipe {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(r.title).font(.headline)
                        Text("\(r.calories) kcal · \(r.protein_g)g protein · \(r.engine)")
                            .font(.caption).foregroundStyle(.secondary)
                        Text(r.instructions).font(.caption)
                    }
                    .padding(.vertical, 4)
                }
            }

            Section("Pantry & Fridge (\(items.count))") {
                ForEach(items) { item in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(item.name).font(.subheadline.weight(.medium))
                            Text("\(String(format: "%.1f", item.quantity)) \(item.unit) · \(item.category)")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Text(item.is_staple ? "Staple" : "\(item.days_remaining)d left")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(item.is_expiring_soon ? Color.orange : Color.secondary)
                    }
                    .swipeActions {
                        Button(role: .destructive) {
                            Task {
                                try? await APIClient.shared.deleteKitchenItem(id: item.id)
                                await load()
                            }
                        } label: { Label("Delete", systemImage: "trash") }
                    }
                }
            }

            Section("Shopping List") {
                HStack {
                    TextField("Add grocery item...", text: $newShopText)
                    Button("Add") {
                        let name = newShopText.trimmingCharacters(in: .whitespacesAndNewlines)
                        guard !name.isEmpty else { return }
                        Task {
                            _ = try? await APIClient.shared.addShoppingItem(name: name)
                            newShopText = ""
                            await load()
                        }
                    }
                    .disabled(newShopText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
                ForEach(shopping) { s in
                    Button {
                        Task {
                            _ = try? await APIClient.shared.toggleShoppingItem(id: s.id)
                            await load()
                        }
                    } label: {
                        HStack {
                            Image(systemName: s.is_checked ? "checkmark.circle.fill" : "circle")
                                .foregroundStyle(s.is_checked ? Color.green : Color.secondary)
                            Text(s.name).strikethrough(s.is_checked).foregroundStyle(.primary)
                        }
                    }
                    .swipeActions {
                        Button(role: .destructive) {
                            Task {
                                try? await APIClient.shared.deleteShoppingItem(id: s.id)
                                await load()
                            }
                        } label: { Label("Delete", systemImage: "trash") }
                    }
                }
            }
        }
        .navigationTitle("Kitchen")
        .refreshable { await load() }
        .task { await load() }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { showAdd = true } label: { Image(systemName: "plus") }
            }
        }
        .sheet(isPresented: $showAdd) {
            AddKitchenItemSheet { await load() }
        }
    }

    private func load() async {
        do {
            async let k = APIClient.shared.fetchKitchenItems()
            async let s = APIClient.shared.fetchShoppingList()
            (items, shopping) = try await (k, s)
        } catch { errorMsg = error.localizedDescription }
    }

    private func makeRecipe() async {
        isLoading = true; errorMsg = nil
        defer { isLoading = false }
        do { recipe = try await APIClient.shared.generateDinnerRecipe() }
        catch { errorMsg = error.localizedDescription }
    }
}

private struct AddKitchenItemSheet: View {
    @Environment(\.dismiss) private var dismiss
    var onSaved: () async -> Void
    @State private var name = ""
    @State private var category = "Produce"
    @State private var qty = "1"
    @State private var unit = "pcs"
    @State private var shelfDays = 7

    var body: some View {
        NavigationStack {
            Form {
                TextField("Item Name", text: $name)
                TextField("Category", text: $category)
                TextField("Quantity", text: $qty).keyboardType(.decimalPad)
                TextField("Unit", text: $unit)
                Stepper("Shelf life: \(shelfDays) days", value: $shelfDays, in: 1...365)
            }
            .navigationTitle("Add Kitchen Item")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        Task {
                            _ = try? await APIClient.shared.addKitchenItem(
                                name: name, category: category,
                                quantity: Double(qty) ?? 1, unit: unit, shelfLife: shelfDays
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
