import SwiftUI
import WidgetKit

@MainActor
final class AppState: ObservableObject {
    @Published var isAuthenticated: Bool = SharedConfig.loadCredentials() != nil
    @Published var userEmail: String = SharedConfig.loadCredentials()?.email ?? ""
    @Published var selectedMonth: String = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM"
        return f.string(from: Date())
    }()
    @Published var availableMonths: [String] = []
    @Published var summary: WealthSummary?
    @Published var widgetSummary: WidgetSummary?
    @Published var entries: [LedgerEntry] = []
    @Published var categories: [String] = []
    @Published var assistantItems: [AssistantItem] = []
    @Published var morningBriefing: MorningBriefing?
    @Published var widgetToken: String = ""
    @Published var ntfyTopicURL: String = ""
    @Published var isLoading = false
    @Published var errorMessage: String?
    @Published var statusMessage: String?

    func signIn(baseURL: String, email: String, password: String) async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            let res = try await APIClient.shared.mobileLogin(baseURL: baseURL, email: email, password: password)
            SharedConfig.saveSession(baseURL: baseURL, token: res.access_token, email: res.user.email)
            userEmail = res.user.email
            isAuthenticated = true
            WidgetCenter.shared.reloadAllTimelines()
            await loadAll()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func loadAll() async {
        guard isAuthenticated else { return }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            async let s = APIClient.shared.fetchSummary(month: selectedMonth)
            async let w = APIClient.shared.fetchWidgetSummary()
            async let e = APIClient.shared.fetchEntries(month: selectedMonth)
            async let c = APIClient.shared.fetchCategories()
            let (sum, wid, ent, cats) = try await (s, w, e, c)
            summary = sum
            widgetSummary = wid
            entries = ent
            categories = cats.map(\.name)
            if availableMonths.isEmpty, let allSum = try? await APIClient.shared.fetchSummary(month: "") {
                let months = (allSum.monthly_series ?? []).map(\.month).sorted(by: >)
                if !months.isEmpty {
                    availableMonths = months
                }
            }
            if !selectedMonth.isEmpty && !availableMonths.contains(selectedMonth) {
                availableMonths = ([selectedMonth] + availableMonths).sorted(by: >)
            }
            WidgetCenter.shared.reloadAllTimelines()
            if let cfg = try? await APIClient.shared.fetchWidgetConfig() {
                widgetToken = cfg.widget_api_token
                ntfyTopicURL = cfg.ntfy_topic_url
            }
            await loadAssistant()
        } catch APIError.notAuthenticated {
            SharedConfig.clearSession()
            isAuthenticated = false
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func selectMonth(_ month: String) async {
        selectedMonth = month
        await loadAll()
    }

    func syncNow() async {
        isLoading = true
        statusMessage = nil
        errorMessage = nil
        defer { isLoading = false }
        do {
            let res = try await APIClient.shared.syncNow()
            statusMessage = "Synced +\(res.inserted_count) new (\(res.skipped_duplicates) dup)"
            await loadAll()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func updateCategory(entryId: Int, category: String) async {
        do {
            _ = try await APIClient.shared.updateEntryCategory(id: entryId, category: category)
            await loadAll()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func toggleType(entryId: Int) async {
        do {
            _ = try await APIClient.shared.toggleEntryType(id: entryId)
            await loadAll()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func toggleSelfTransfer(entryId: Int, isSelf: Bool) async {
        do {
            _ = try await APIClient.shared.toggleSelfTransfer(id: entryId, isSelf: isSelf)
            await loadAll()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func deleteEntry(entryId: Int) async {
        do {
            try await APIClient.shared.deleteEntry(id: entryId)
            await loadAll()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func addManual(merchant: String, category: String, txType: String, amount: Double, occurredAt: Date, account: String, remark: String) async -> Bool {
        let f = ISO8601DateFormatter()
        let payload = ManualLedgerRequest(
            merchant: merchant,
            category: category,
            tx_type: txType,
            amount_myr: amount,
            occurred_at: f.string(from: occurredAt),
            account_name: account,
            remark: remark
        )
        do {
            _ = try await APIClient.shared.addManualEntry(payload)
            await loadAll()
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }

    func loadAssistant() async {
        guard isAuthenticated else { return }
        do {
            async let b = APIClient.shared.fetchMorningBriefing()
            async let items = APIClient.shared.fetchAssistantItems()
            let (brief, list) = try await (b, items)
            morningBriefing = brief
            assistantItems = list
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func sendAssistantMessage(_ message: String) async -> Bool {
        do {
            _ = try await APIClient.shared.ingestAssistantMessage(message)
            await loadAssistant()
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }

    func setAssistantStatus(id: Int, status: String) async {
        do {
            _ = try await APIClient.shared.updateAssistantItem(id: id, status: status)
            await loadAssistant()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func deleteAssistantItem(id: Int) async {
        do {
            try await APIClient.shared.deleteAssistantItem(id: id)
            await loadAssistant()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func signOut() async {
        await APIClient.shared.logout()
        isAuthenticated = false
        summary = nil
        widgetSummary = nil
        entries = []
        WidgetCenter.shared.reloadAllTimelines()
    }
}

@main
struct PersonalERPApp: App {
    @StateObject private var appState = AppState()

    var body: some Scene {
        WindowGroup {
            Group {
                if appState.isAuthenticated {
                    MainTabView()
                        .environmentObject(appState)
                        .task { await appState.loadAll() }
                } else {
                    LoginView()
                        .environmentObject(appState)
                }
            }
            .preferredColorScheme(.dark)
        }
    }
}

struct MainTabView: View {
    @EnvironmentObject private var appState: AppState
    @State private var selectedTab = 0
    @State private var showQuickAdd = false

    var body: some View {
        ZStack(alignment: .bottomLeading) {
            TabView(selection: $selectedTab) {
                DashboardView()
                    .tabItem { Label("Wealth", systemImage: "chart.pie.fill") }
                    .tag(0)

                TransactionsView()
                    .tabItem { Label("Transactions", systemImage: "list.bullet.rectangle") }
                    .tag(1)

                AssistantView()
                    .tabItem { Label("Assistant", systemImage: "sparkles") }
                    .tag(2)

                SettingsView()
                    .tabItem { Label("Settings", systemImage: "gearshape.fill") }
                    .tag(3)
            }
            .tint(Color(red: 0.51, green: 0.55, blue: 0.97))

            if selectedTab == 0 || selectedTab == 1 {
                Button {
                    showQuickAdd = true
                } label: {
                    Image(systemName: "plus")
                        .font(.title2.weight(.bold))
                        .foregroundStyle(.white)
                        .frame(width: 52, height: 52)
                        .background(Color(red: 0.37, green: 0.42, blue: 0.82))
                        .clipShape(Circle())
                        .shadow(color: .black.opacity(0.4), radius: 8, x: 0, y: 4)
                }
                .padding(.leading, 18)
                .padding(.bottom, 64)
                .accessibilityLabel("Add Transaction")
            }
        }
        .sheet(isPresented: $showQuickAdd) {
            AddTransactionSheet()
                .environmentObject(appState)
        }
    }
}
