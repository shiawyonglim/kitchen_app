import Foundation

enum APIError: LocalizedError {
    case notAuthenticated
    case invalidURL
    case server(Int, String)

    var errorDescription: String? {
        switch self {
        case .notAuthenticated:
            return "Please sign in again."
        case .invalidURL:
            return "Invalid server URL."
        case .server(let code, let msg):
            return "HTTP \(code): \(msg)"
        }
    }
}

final class APIClient {
    static let shared = APIClient()
    private let decoder = JSONDecoder()
    private let encoder = JSONEncoder()

    private func makeRequest(
        path: String,
        method: String = "GET",
        body: Data? = nil,
        requireAuth: Bool = true
    ) throws -> URLRequest {
        guard let creds = SharedConfig.loadCredentials() else {
            throw APIError.notAuthenticated
        }
        guard let url = URL(string: "\(creds.baseURL)\(path)") else {
            throw APIError.invalidURL
        }
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.timeoutInterval = 15
        if requireAuth {
            req.setValue("Bearer \(creds.token)", forHTTPHeaderField: "Authorization")
        }
        if let body {
            req.httpBody = body
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return req
    }

    private func perform<T: Decodable>(_ req: URLRequest, as type: T.Type) async throws -> T {
        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse else {
            throw APIError.server(0, "No HTTP response")
        }
        if http.statusCode == 401 {
            throw APIError.notAuthenticated
        }
        guard (200..<300).contains(http.statusCode) else {
            let msg = String(data: data, encoding: .utf8) ?? "Request failed"
            throw APIError.server(http.statusCode, msg)
        }
        return try decoder.decode(T.self, from: data)
    }

    func mobileLogin(baseURL: String, email: String, password: String) async throws -> MobileLoginResponse {
        let cleanBase = baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard let url = URL(string: "\(cleanBase)/api/auth/mobile-login") else {
            throw APIError.invalidURL
        }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.timeoutInterval = 15
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let payload = ["email": email, "password": password]
        req.httpBody = try encoder.encode(payload)
        return try await perform(req, as: MobileLoginResponse.self)
    }

    func fetchWidgetSummary() async throws -> WidgetSummary {
        let req = try makeRequest(path: "/api/wealth/widget-summary")
        return try await perform(req, as: WidgetSummary.self)
    }

    func fetchSummary(month: String) async throws -> WealthSummary {
        let suffix = month.isEmpty ? "" : "?month=\(month)"
        let req = try makeRequest(path: "/api/wealth/summary\(suffix)")
        return try await perform(req, as: WealthSummary.self)
    }

    func fetchEntries(month: String) async throws -> [LedgerEntry] {
        let suffix = month.isEmpty ? "" : "?month=\(month)"
        let req = try makeRequest(path: "/api/wealth/entries\(suffix)")
        return try await perform(req, as: [LedgerEntry].self)
    }

    func fetchCategories() async throws -> [CategoryRule] {
        let req = try makeRequest(path: "/api/wealth/categories")
        return try await perform(req, as: [CategoryRule].self)
    }

    func updateEntryCategory(id: Int, category: String) async throws -> LedgerEntry {
        let body = try encoder.encode(["category": category])
        let req = try makeRequest(path: "/api/wealth/entries/\(id)", method: "PATCH", body: body)
        return try await perform(req, as: LedgerEntry.self)
    }

    func toggleEntryType(id: Int) async throws -> LedgerEntry {
        let req = try makeRequest(path: "/api/wealth/entries/\(id)/toggle-type", method: "PATCH")
        return try await perform(req, as: LedgerEntry.self)
    }

    func toggleSelfTransfer(id: Int, isSelf: Bool) async throws -> LedgerEntry {
        let body = try encoder.encode(["is_self_transfer": isSelf])
        let req = try makeRequest(path: "/api/wealth/entries/\(id)", method: "PATCH", body: body)
        return try await perform(req, as: LedgerEntry.self)
    }

    func deleteEntry(id: Int) async throws {
        let req = try makeRequest(path: "/api/wealth/entries/\(id)", method: "DELETE")
        let (_, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw APIError.server(0, "Delete failed")
        }
    }

    func addManualEntry(_ payload: ManualLedgerRequest) async throws -> LedgerEntry {
        let body = try encoder.encode(payload)
        let req = try makeRequest(path: "/api/wealth/entries/manual", method: "POST", body: body)
        return try await perform(req, as: LedgerEntry.self)
    }

    func syncNow() async throws -> SyncResult {
        let req = try makeRequest(path: "/api/wealth/sync", method: "POST", body: Data("{}".utf8))
        return try await perform(req, as: SyncResult.self)
    }

    func testNotification() async throws -> NotificationTestResponse {
        let req = try makeRequest(path: "/api/wealth/notifications/test", method: "POST")
        return try await perform(req, as: NotificationTestResponse.self)
    }

    func fetchWidgetConfig() async throws -> WidgetConfigResponse {
        let req = try makeRequest(path: "/api/wealth/widget-config")
        return try await perform(req, as: WidgetConfigResponse.self)
    }

    func fetchMorningBriefing() async throws -> MorningBriefing {
        let req = try makeRequest(path: "/api/assistant/morning-briefing")
        return try await perform(req, as: MorningBriefing.self)
    }

    func fetchAssistantItems(status: String? = nil) async throws -> [AssistantItem] {
        let suffix = status.map { "?status=\($0)" } ?? ""
        let req = try makeRequest(path: "/api/assistant/items\(suffix)")
        return try await perform(req, as: [AssistantItem].self)
    }

    func ingestAssistantMessage(_ message: String) async throws -> [AssistantItem] {
        let payload = ["raw_message": message, "source": "ios", "sender": "iPhone"]
        let body = try encoder.encode(payload)
        let req = try makeRequest(path: "/api/assistant/ingest", method: "POST", body: body)
        return try await perform(req, as: [AssistantItem].self)
    }

    func updateAssistantItem(id: Int, status: String) async throws -> AssistantItem {
        let body = try encoder.encode(["status": status])
        let req = try makeRequest(path: "/api/assistant/items/\(id)", method: "PATCH", body: body)
        return try await perform(req, as: AssistantItem.self)
    }

    func deleteAssistantItem(id: Int) async throws {
        let req = try makeRequest(path: "/api/assistant/items/\(id)", method: "DELETE")
        let (_, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw APIError.server(0, "Delete failed")
        }
    }

    func logout() async {
        if let req = try? makeRequest(path: "/api/auth/logout", method: "POST") {
            _ = try? await URLSession.shared.data(for: req)
        }
        SharedConfig.clearSession()
    }
}
