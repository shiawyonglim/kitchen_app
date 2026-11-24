import SwiftUI
import UIKit
import WidgetKit

struct SettingsView: View {
    @EnvironmentObject private var appState: AppState
    @State private var appSettings: AppSettingsModel?
    @State private var budgetText = ""
    @State private var savingsText = ""
    @State private var caloriesText = ""
    @State private var proteinText = ""
    @State private var testResult: String?

    var body: some View {
        NavigationStack {
            Form {
                Section("Account") {
                    LabeledContent("Signed in", value: appState.userEmail.isEmpty ? "User" : appState.userEmail)
                    LabeledContent("Server", value: SharedConfig.lastBaseURL())
                }

                if var cfg = appSettings {
                    Section("Budgets & Targets") {
                        LabeledContent("Budget (RM)") {
                            TextField("3000", text: $budgetText)
                                .keyboardType(.decimalPad)
                                .multilineTextAlignment(.trailing)
                        }
                        LabeledContent("Savings Target") {
                            TextField("300", text: $savingsText)
                                .keyboardType(.decimalPad)
                                .multilineTextAlignment(.trailing)
                        }
                        LabeledContent("Daily Calories") {
                            TextField("2000", text: $caloriesText)
                                .keyboardType(.numberPad)
                                .multilineTextAlignment(.trailing)
                        }
                        LabeledContent("Daily Protein (g)") {
                            TextField("120", text: $proteinText)
                                .keyboardType(.numberPad)
                                .multilineTextAlignment(.trailing)
                        }
                        Toggle("Auto-Sync Emails", isOn: Binding(
                            get: { cfg.auto_sync_enabled },
                            set: { cfg.auto_sync_enabled = $0; appSettings = cfg }
                        ))
                        Toggle("Live Gmail Listener", isOn: Binding(
                            get: { cfg.live_listener_enabled },
                            set: { cfg.live_listener_enabled = $0; appSettings = cfg }
                        ))
                        Toggle("Auto-Learn Categories", isOn: Binding(
                            get: { cfg.auto_learn_categories },
                            set: { cfg.auto_learn_categories = $0; appSettings = cfg }
                        ))
                        Button("Save App Settings") {
                            Task { await saveSettings() }
                        }
                    }
                }

                Section("Widget Setup") {
                    Button("Copy Server URL") {
                        UIPasteboard.general.string = SharedConfig.lastBaseURL()
                        testResult = "Copied Server URL to clipboard."
                    }

                    Button("Copy Widget Token") {
                        Task {
                            if appState.widgetToken.isEmpty,
                               let cfg = try? await APIClient.shared.fetchWidgetConfig() {
                                appState.widgetToken = cfg.widget_api_token
                            }
                            if appState.widgetToken.isEmpty {
                                testResult = "WIDGET_API_TOKEN is empty on server .env."
                            } else {
                                UIPasteboard.general.string = appState.widgetToken
                                testResult = "Copied Widget Token to clipboard."
                            }
                        }
                    }

                    Button("Refresh Home/Lock Screen Widgets") {
                        WidgetCenter.shared.reloadAllTimelines()
                        testResult = "Widget timeline reloaded."
                    }
                }

                Section("Push Notifications (ntfy)") {
                    Button("Send Test ntfy Push Notification") {
                        Task {
                            do {
                                let res = try await APIClient.shared.testNotification()
                                if !res.configured {
                                    testResult = "Set NTFY_TOPIC_URL in server .env first."
                                } else {
                                    testResult = res.sent ? "Test push sent to ntfy!" : "Failed to send ntfy push."
                                }
                            } catch {
                                testResult = error.localizedDescription
                            }
                        }
                    }

                    if !appState.ntfyTopicURL.isEmpty {
                        LabeledContent("Topic", value: appState.ntfyTopicURL.replacingOccurrences(of: "https://ntfy.sh/", with: ""))
                    }
                }

                if let msg = testResult {
                    Section {
                        Text(msg)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }

                Section {
                    Button("Sign Out", role: .destructive) {
                        Task { await appState.signOut() }
                    }
                }
            }
            .navigationTitle("Settings")
            .task { await loadSettings() }
        }
    }

    private func loadSettings() async {
        guard let cfg = try? await APIClient.shared.fetchAppSettings() else { return }
        appSettings = cfg
        budgetText = String(format: "%.0f", cfg.monthly_budget_myr)
        savingsText = String(format: "%.0f", cfg.monthly_savings_target_myr)
        caloriesText = "\(cfg.default_calories)"
        proteinText = "\(cfg.default_protein_g)"
    }

    private func saveSettings() async {
        guard var cfg = appSettings else { return }
        if let b = Double(budgetText), b > 0 { cfg.monthly_budget_myr = b }
        if let s = Double(savingsText), s >= 0 { cfg.monthly_savings_target_myr = s }
        if let c = Int(caloriesText), c >= 800 { cfg.default_calories = c }
        if let p = Int(proteinText), p >= 30 { cfg.default_protein_g = p }
        do {
            let saved = try await APIClient.shared.updateAppSettings(cfg)
            appSettings = saved
            testResult = "Settings saved."
            await appState.loadAll()
        } catch {
            testResult = error.localizedDescription
        }
    }
}
