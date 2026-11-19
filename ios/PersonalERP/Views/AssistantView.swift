import SwiftUI

struct AssistantView: View {
    @EnvironmentObject private var appState: AppState
    @State private var promptText = ""
    @State private var isSending = false

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                List {
                    if let brief = appState.morningBriefing {
                        Section("AI Briefing · \(brief.date_label)") {
                            VStack(alignment: .leading, spacing: 6) {
                                Text(brief.greeting)
                                    .font(.subheadline.weight(.semibold))
                                if !brief.ai_focus_plan.isEmpty {
                                    Text(brief.ai_focus_plan)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                                if !brief.expiring_kitchen.isEmpty {
                                    Text("Expiring soon: \(brief.expiring_kitchen.joined(separator: ", "))")
                                        .font(.caption2)
                                        .foregroundStyle(.orange)
                                }
                            }
                            .padding(.vertical, 2)
                        }
                    }

                    let pending = appState.assistantItems.filter { $0.status == "pending" }
                    Section("Action Items (\(pending.count))") {
                        if pending.isEmpty {
                            Text("No pending tasks. Send a note, reminder, or expense below.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        } else {
                            ForEach(pending) { item in
                                AssistantItemRow(item: item)
                            }
                        }
                    }

                    let done = appState.assistantItems.filter { $0.status == "done" }.prefix(10)
                    if !done.isEmpty {
                        Section("Completed") {
                            ForEach(Array(done)) { item in
                                AssistantItemRow(item: item)
                            }
                        }
                    }
                }
                .listStyle(.insetGrouped)
                .refreshable {
                    await appState.loadAssistant()
                }

                HStack(spacing: 8) {
                    TextField("Ask AI or add task / note...", text: $promptText, axis: .vertical)
                        .lineLimit(1...3)
                        .textFieldStyle(.roundedBorder)

                    Button {
                        let msg = promptText.trimmingCharacters(in: .whitespacesAndNewlines)
                        guard !msg.isEmpty else { return }
                        isSending = true
                        Task {
                            let ok = await appState.sendAssistantMessage(msg)
                            if ok { promptText = "" }
                            isSending = false
                        }
                    } label: {
                        if isSending {
                            ProgressView()
                                .frame(width: 28, height: 28)
                        } else {
                            Image(systemName: "arrow.up.circle.fill")
                                .font(.title2)
                        }
                    }
                    .disabled(promptText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isSending)
                }
                .padding(.horizontal)
                .padding(.vertical, 10)
                .background(.ultraThinMaterial)
            }
            .navigationTitle("AI Assistant")
            .task {
                if appState.assistantItems.isEmpty && appState.morningBriefing == nil {
                    await appState.loadAssistant()
                }
            }
        }
    }
}

private struct AssistantItemRow: View {
    @EnvironmentObject private var appState: AppState
    let item: AssistantItem

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Button {
                let nextStatus = item.status == "done" ? "pending" : "done"
                Task { await appState.setAssistantStatus(id: item.id, status: nextStatus) }
            } label: {
                Image(systemName: item.status == "done" ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(item.status == "done" ? .green : .secondary)
                    .font(.title3)
            }
            .buttonStyle(.plain)

            VStack(alignment: .leading, spacing: 3) {
                Text(item.title)
                    .font(.subheadline.weight(.medium))
                    .strikethrough(item.status == "done")
                if !item.ai_plan.isEmpty {
                    Text(item.ai_plan)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(3)
                }
                HStack(spacing: 6) {
                    Text(item.category.capitalized)
                    if !item.scheduled_for.isEmpty {
                        Text("· \(item.scheduled_for)")
                    }
                    if item.priority == "high" {
                        Text("· High")
                            .foregroundStyle(.red)
                    }
                }
                .font(.caption2)
                .foregroundStyle(.secondary)
            }
        }
        .swipeActions(edge: .trailing) {
            Button(role: .destructive) {
                Task { await appState.deleteAssistantItem(id: item.id) }
            } label: {
                Label("Delete", systemImage: "trash")
            }
        }
    }
}
