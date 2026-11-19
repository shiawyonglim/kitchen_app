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
    @Published var summary: WealthSummary?
    @Published var widgetSummary: WidgetSummary?
    @Published var entries: [LedgerEntry] = []
    @Published var categories: [String] = []
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
            WidgetCenter.shared.reloadAllTimelines()
        } catch APIError.notAuthenticated {
            SharedConfig.clearSession()
            isAuthenticated = false
        } catch {
            errorMessage = error.localizedDescription
        }
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

    func addManual(merchant: String, category: String, txType: String, amount: Double, account: String, remark: String) async -> Bool {
        let f = ISO8601DateFormatter()
        let payload = ManualLedgerRequest(
            merchant: merchant,
            category: category,
            tx_type: txType,
            amount_myr: amount,
            occurred_at: f.string(from: Date()),
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

    var body: some View {
        TabView {
            DashboardView()
                .tabItem { Label("Wealth", systemImage: "chart.pie.fill") }

            TransactionsView()
                .tabItem { Label("Transactions", systemImage: "list.bullet.rectangle") }

            SettingsView()
                .tabItem { Label("Settings", systemImage: "gearshape.fill") }
        }
        .tint(Color(red: 0.51, green: 0.55, blue: 0.97))
    }
}
