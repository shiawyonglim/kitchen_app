import SwiftUI
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

                Section("Widgets & Notifications") {
                    Button("Refresh Home/Lock Screen Widgets") {
                        WidgetCenter.shared.reloadAllTimelines()
                        testResult = "Widget timeline reloaded."
                    }

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

                    if let msg = testResult {
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
