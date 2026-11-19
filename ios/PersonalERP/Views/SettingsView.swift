import SwiftUI
import UIKit
import WidgetKit

struct SettingsView: View {
    @EnvironmentObject private var appState: AppState
    @State private var testResult: String?

    var body: some View {
        NavigationStack {
            Form {
                Section("Account") {
                    LabeledContent("Signed in", value: appState.userEmail.isEmpty ? "User" : appState.userEmail)
                    LabeledContent("Server", value: SharedConfig.lastBaseURL())
                }

                Section("Widget Setup (Long-press Widget -> Edit Widget)") {
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
                                testResult = "Copied Widget Token! Long-press widget -> Edit Widget -> paste it."
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
        }
    }
}
