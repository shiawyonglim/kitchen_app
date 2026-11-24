import SwiftUI

struct CaloriesView: View {
    @State private var summary: CalorieSummary?
    @State private var entries: [CalorieEntry] = []
    @State private var foodText = ""
    @State private var mealType = "lunch"
    @State private var pendingConfirm: CalorieParseResponse?
    @State private var isLogging = false
    @State private var errorMsg: String?

    var body: some View {
        List {
            if let err = errorMsg {
                Section { Text(err).font(.caption).foregroundStyle(.red) }
            }

            if let s = summary {
                Section("Today (\(s.goal_mode))") {
                    LabeledContent("Calories") {
                        Text("\(s.today_calories) / \(s.daily_target_calories) kcal")
                            .fontWeight(.bold).monospacedDigit()
                            .foregroundStyle(s.today_calories > s.daily_target_calories ? .orange : .green)
                    }
                    LabeledContent("Protein") {
                        Text("\(String(format: "%.0f", s.today_protein_g)) / \(s.daily_target_protein_g)g")
                            .monospacedDigit()
                    }
                    LabeledContent("Carbs / Fat") {
                        Text("\(String(format: "%.0f", s.today_carbs_g))g C · \(String(format: "%.0f", s.today_fat_g))g F")
                            .monospacedDigit().foregroundStyle(.secondary)
                    }
                }
            }

            Section("Quick AI Food Log") {
                TextField("e.g. 200g chicken breast", text: $foodText)
                Picker("Meal", selection: $mealType) {
                    Text("Breakfast").tag("breakfast")
                    Text("Lunch").tag("lunch")
                    Text("Dinner").tag("dinner")
                    Text("Snack").tag("snack")
                }
                .pickerStyle(.segmented)
                Button(isLogging ? "Logging..." : "Log Food") {
                    Task { await logFood() }
                }
                .disabled(foodText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isLogging)
            }

            Section("Logged Meals (\(entries.count))") {
                ForEach(entries) { e in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(e.food_name).font(.subheadline.weight(.medium))
                            Text("\(e.meal_type.capitalized) · \(Int(e.grams))g · \(String(format: "%.0f", e.protein_g))g P")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Text("\(e.calories) kcal").font(.subheadline.weight(.semibold).monospacedDigit())
                    }
                    .swipeActions {
                        Button(role: .destructive) {
                            Task {
                                try? await APIClient.shared.deleteCalorieEntry(id: e.id)
                                await load()
                            }
                        } label: { Label("Delete", systemImage: "trash") }
                    }
                }
            }
        }
        .navigationTitle("Calories")
        .refreshable { await load() }
        .task { await load() }
        .alert("Confirm Food Macros", isPresented: Binding(
            get: { pendingConfirm != nil },
            set: { if !$0 { pendingConfirm = nil } }
        )) {
            Button("Confirm & Save") {
                if let p = pendingConfirm, let est = p.suggested_per_100g {
                    Task {
                        _ = try? await APIClient.shared.confirmFood(
                            name: p.food_name, grams: p.grams,
                            kcal100: est.kcal_per_100g, prot100: est.protein_per_100g,
                            carbs100: est.carbs_per_100g, fat100: est.fat_per_100g,
                            mealType: p.meal_type
                        )
                        pendingConfirm = nil
                        foodText = ""
                        await load()
                    }
                }
            }
            Button("Cancel", role: .cancel) { pendingConfirm = nil }
        } message: {
            if let p = pendingConfirm, let est = p.suggested_per_100g {
                Text("\(p.food_name) (\(Int(p.grams))g): ~\(Int(est.kcal_per_100g)) kcal & \(Int(est.protein_per_100g))g protein per 100g")
            }
        }
    }

    private func load() async {
        do {
            async let s = APIClient.shared.fetchCalorieSummary()
            async let e = APIClient.shared.fetchCalorieEntries()
            (summary, entries) = try await (s, e)
        } catch { errorMsg = error.localizedDescription }
    }

    private func logFood() async {
        isLogging = true; errorMsg = nil
        defer { isLogging = false }
        do {
            let res = try await APIClient.shared.logCalorieText(text: foodText, mealType: mealType)
            if res.status == "needs_input" {
                pendingConfirm = res
            } else {
                foodText = ""
                await load()
            }
        } catch { errorMsg = error.localizedDescription }
    }
}
