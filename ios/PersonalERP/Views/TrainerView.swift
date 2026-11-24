import SwiftUI

struct TrainerView: View {
    @State private var analytics: TrainerAnalytics?
    @State private var workouts: [WorkoutLog] = []
    @State private var measurements: [BodyMeasurement] = []
    @State private var showAddWorkout = false
    @State private var weightText = ""
    @State private var waistText = ""
    @State private var errorMsg: String?

    var body: some View {
        List {
            if let err = errorMsg {
                Section { Text(err).font(.caption).foregroundStyle(.red) }
            }

            if let an = analytics {
                Section("Fitness Summary (\(an.goal.goal_mode))") {
                    LabeledContent("Total Sessions", value: "\(an.total_sessions)")
                    LabeledContent("Calories Burned", value: "\(an.total_calories_burned) kcal")
                    LabeledContent("Distance", value: "\(String(format: "%.1f", an.total_distance_km)) km")
                    LabeledContent("Daily Targets", value: "\(an.goal.target_calories) kcal · \(an.goal.target_protein_g)g P")
                }
            }

            Section("Log Body Measurement") {
                HStack {
                    TextField("Weight (kg)", text: $weightText).keyboardType(.decimalPad)
                    TextField("Waist (cm)", text: $waistText).keyboardType(.decimalPad)
                    Button("Save") {
                        Task {
                            _ = try? await APIClient.shared.logMeasurement(
                                weightKg: Double(weightText), waistCm: Double(waistText)
                            )
                            weightText = ""; waistText = ""
                            await load()
                        }
                    }
                    .disabled(Double(weightText) == nil && Double(waistText) == nil)
                }
                if let latest = measurements.first {
                    Text("Latest: \(latest.weight_kg.map { String(format: "%.1f kg", $0) } ?? "—") · \(latest.waist_cm.map { String(format: "%.1f cm", $0) } ?? "—")")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }

            Section("Workouts (\(workouts.count))") {
                ForEach(workouts) { w in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(w.exercise_type).font(.subheadline.weight(.medium))
                            Text("\(String(w.logged_at.prefix(10))) · \(w.reps) reps · \(Int(w.duration_min))m")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Text("\(w.calories_burned) kcal").font(.subheadline.weight(.semibold).monospacedDigit())
                    }
                    .swipeActions {
                        Button(role: .destructive) {
                            Task {
                                try? await APIClient.shared.deleteWorkout(id: w.id)
                                await load()
                            }
                        } label: { Label("Delete", systemImage: "trash") }
                    }
                }
            }
        }
        .navigationTitle("Trainer")
        .refreshable { await load() }
        .task { await load() }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { showAddWorkout = true } label: { Image(systemName: "plus") }
            }
        }
        .sheet(isPresented: $showAddWorkout) {
            AddWorkoutSheet { await load() }
        }
    }

    private func load() async {
        do {
            async let a = APIClient.shared.fetchTrainerAnalytics()
            async let w = APIClient.shared.fetchWorkouts()
            async let m = APIClient.shared.fetchMeasurements()
            (analytics, workouts, measurements) = try await (a, w, m)
        } catch { errorMsg = error.localizedDescription }
    }
}

private struct AddWorkoutSheet: View {
    @Environment(\.dismiss) private var dismiss
    var onSaved: () async -> Void
    @State private var exercise = "Strength Training"
    @State private var reps = "30"
    @State private var mins = "30"
    @State private var km = "0"

    var body: some View {
        NavigationStack {
            Form {
                TextField("Exercise Type", text: $exercise)
                TextField("Reps", text: $reps).keyboardType(.numberPad)
                TextField("Duration (min)", text: $mins).keyboardType(.decimalPad)
                TextField("Distance (km)", text: $km).keyboardType(.decimalPad)
            }
            .navigationTitle("Log Workout")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        Task {
                            try? await APIClient.shared.logWorkout(
                                exerciseType: exercise,
                                reps: Int(reps) ?? 0,
                                durationMin: Double(mins) ?? 0,
                                distanceKm: Double(km) ?? 0
                            )
                            await onSaved()
                            dismiss()
                        }
                    }.disabled(exercise.isEmpty)
                }
            }
        }
    }
}
