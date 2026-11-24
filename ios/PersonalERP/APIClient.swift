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
        let data = try await send(req)
        return try decoder.decode(T.self, from: data)
    }

    private func performVoid(_ req: URLRequest) async throws {
        _ = try await send(req)
    }

    private func send(_ req: URLRequest) async throws -> Data {
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
        return data
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
        try await performVoid(try makeRequest(path: "/api/wealth/entries/\(id)", method: "DELETE"))
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
        try await performVoid(try makeRequest(path: "/api/assistant/items/\(id)", method: "DELETE"))
    }

    // MARK: - Finance
    func fetchFinanceOverview(month: String) async throws -> FinanceOverview {
        let suffix = month.isEmpty ? "" : "?month=\(month)"
        return try await perform(try makeRequest(path: "/api/finance/overview\(suffix)"), as: FinanceOverview.self)
    }

    func fetchFinanceCoach(month: String) async throws -> FinanceCoachResponse {
        let body = try encoder.encode(["month": month])
        return try await perform(try makeRequest(path: "/api/finance/coach", method: "POST", body: body), as: FinanceCoachResponse.self)
    }

    // MARK: - Assets
    func fetchAssets() async throws -> [TrackedAsset] {
        try await perform(try makeRequest(path: "/api/assets"), as: [TrackedAsset].self)
    }

    func fetchNetWorth() async throws -> NetWorthSummary {
        try await perform(try makeRequest(path: "/api/assets/net-worth"), as: NetWorthSummary.self)
    }

    func addAsset(name: String, category: String, purchasePrice: Double, quantity: Int) async throws -> TrackedAsset {
        struct Req: Encodable { let name: String; let category: String; let purchase_price_myr: Double; let quantity: Int }
        let body = try encoder.encode(Req(name: name, category: category, purchase_price_myr: purchasePrice, quantity: quantity))
        return try await perform(try makeRequest(path: "/api/assets", method: "POST", body: body), as: TrackedAsset.self)
    }

    func refreshAllAssets() async throws -> [TrackedAsset] {
        try await perform(try makeRequest(path: "/api/assets/refresh-all", method: "POST"), as: [TrackedAsset].self)
    }

    func deleteAsset(id: Int) async throws {
        try await performVoid(try makeRequest(path: "/api/assets/\(id)", method: "DELETE"))
    }

    // MARK: - Kitchen
    func fetchKitchenItems() async throws -> [KitchenItem] {
        try await perform(try makeRequest(path: "/api/kitchen/items"), as: [KitchenItem].self)
    }

    func addKitchenItem(name: String, category: String, quantity: Double, unit: String, shelfLife: Int) async throws -> KitchenItem {
        struct Req: Encodable { let name: String; let category: String; let quantity: Double; let unit: String; let shelf_life_days: Int }
        let body = try encoder.encode(Req(name: name, category: category, quantity: quantity, unit: unit, shelf_life_days: shelfLife))
        return try await perform(try makeRequest(path: "/api/kitchen/items", method: "POST", body: body), as: KitchenItem.self)
    }

    func deleteKitchenItem(id: Int) async throws {
        try await performVoid(try makeRequest(path: "/api/kitchen/items/\(id)", method: "DELETE"))
    }

    func generateDinnerRecipe() async throws -> DinnerRecipe {
        try await perform(try makeRequest(path: "/api/kitchen/recipe", method: "POST"), as: DinnerRecipe.self)
    }

    func fetchShoppingList() async throws -> [ShoppingListItem] {
        try await perform(try makeRequest(path: "/api/kitchen/shopping-list"), as: [ShoppingListItem].self)
    }

    func addShoppingItem(name: String) async throws -> ShoppingListItem {
        let body = try encoder.encode(["name": name])
        return try await perform(try makeRequest(path: "/api/kitchen/shopping-list", method: "POST", body: body), as: ShoppingListItem.self)
    }

    func toggleShoppingItem(id: Int) async throws -> ShoppingListItem {
        try await perform(try makeRequest(path: "/api/kitchen/shopping-list/\(id)/toggle", method: "PATCH"), as: ShoppingListItem.self)
    }

    func deleteShoppingItem(id: Int) async throws {
        try await performVoid(try makeRequest(path: "/api/kitchen/shopping-list/\(id)", method: "DELETE"))
    }

    // MARK: - Calories
    func fetchCalorieSummary() async throws -> CalorieSummary {
        try await perform(try makeRequest(path: "/api/calories/summary"), as: CalorieSummary.self)
    }

    func fetchCalorieEntries() async throws -> [CalorieEntry] {
        try await perform(try makeRequest(path: "/api/calories/entries"), as: [CalorieEntry].self)
    }

    func logCalorieText(text: String, mealType: String) async throws -> CalorieParseResponse {
        let body = try encoder.encode(["text": text, "meal_type": mealType])
        return try await perform(try makeRequest(path: "/api/calories/log", method: "POST", body: body), as: CalorieParseResponse.self)
    }

    func confirmFood(name: String, grams: Double, kcal100: Double, prot100: Double, carbs100: Double, fat100: Double, mealType: String) async throws -> CalorieParseResponse {
        struct Req: Encodable {
            let food_name: String; let grams: Double; let kcal_per_100g: Double
            let protein_per_100g: Double; let carbs_per_100g: Double; let fat_per_100g: Double
            let meal_type: String
        }
        let body = try encoder.encode(Req(food_name: name, grams: grams, kcal_per_100g: kcal100, protein_per_100g: prot100, carbs_per_100g: carbs100, fat_per_100g: fat100, meal_type: mealType))
        return try await perform(try makeRequest(path: "/api/calories/confirm-food", method: "POST", body: body), as: CalorieParseResponse.self)
    }

    func deleteCalorieEntry(id: Int) async throws {
        try await performVoid(try makeRequest(path: "/api/calories/entries/\(id)", method: "DELETE"))
    }

    // MARK: - Trainer
    func fetchTrainerAnalytics() async throws -> TrainerAnalytics {
        try await perform(try makeRequest(path: "/api/trainer/analytics"), as: TrainerAnalytics.self)
    }

    func fetchWorkouts() async throws -> [WorkoutLog] {
        try await perform(try makeRequest(path: "/api/trainer/workouts"), as: [WorkoutLog].self)
    }

    func logWorkout(exerciseType: String, reps: Int, durationMin: Double, distanceKm: Double) async throws {
        struct Req: Encodable { let exercise_type: String; let reps: Int; let duration_min: Double; let distance_km: Double }
        let body = try encoder.encode(Req(exercise_type: exerciseType, reps: reps, duration_min: durationMin, distance_km: distanceKm))
        try await performVoid(try makeRequest(path: "/api/trainer/workouts", method: "POST", body: body))
    }

    func deleteWorkout(id: Int) async throws {
        try await performVoid(try makeRequest(path: "/api/trainer/workouts/\(id)", method: "DELETE"))
    }

    func fetchMeasurements() async throws -> [BodyMeasurement] {
        try await perform(try makeRequest(path: "/api/trainer/measurements"), as: [BodyMeasurement].self)
    }

    func logMeasurement(weightKg: Double?, waistCm: Double?) async throws -> BodyMeasurement {
        struct Req: Encodable { let weight_kg: Double?; let waist_cm: Double? }
        let body = try encoder.encode(Req(weight_kg: weightKg, waist_cm: waistCm))
        return try await perform(try makeRequest(path: "/api/trainer/measurements", method: "POST", body: body), as: BodyMeasurement.self)
    }

    // MARK: - Friction
    func fetchFrictionSummary() async throws -> FrictionSummary {
        try await perform(try makeRequest(path: "/api/friction/summary"), as: FrictionSummary.self)
    }

    func logOdometer(km: Double) async throws {
        struct Req: Encodable { let odometer_km: Double }
        let body = try encoder.encode(Req(odometer_km: km))
        try await performVoid(try makeRequest(path: "/api/friction/odometer", method: "POST", body: body))
    }

    func useGear(id: Int, amount: Double = 1.0) async throws {
        struct Req: Encodable { let amount: Double }
        let body = try encoder.encode(Req(amount: amount))
        try await performVoid(try makeRequest(path: "/api/friction/gear/\(id)/use", method: "POST", body: body))
    }

    func resetGear(id: Int) async throws {
        try await performVoid(try makeRequest(path: "/api/friction/gear/\(id)/reset", method: "POST"))
    }

    func createDebt(eventName: String, borrower: String, amount: Double, direction: String) async throws -> SocialDebt {
        struct Req: Encodable { let event_name: String; let borrower_name: String; let amount_myr: Double; let direction: String }
        let body = try encoder.encode(Req(event_name: eventName, borrower_name: borrower, amount_myr: amount, direction: direction))
        return try await perform(try makeRequest(path: "/api/friction/debts", method: "POST", body: body), as: SocialDebt.self)
    }

    func settleDebt(id: Int) async throws {
        try await performVoid(try makeRequest(path: "/api/friction/debts/\(id)/settle", method: "POST"))
    }

    func deleteDebt(id: Int) async throws {
        try await performVoid(try makeRequest(path: "/api/friction/debts/\(id)", method: "DELETE"))
    }

    // MARK: - Settings
    func fetchAppSettings() async throws -> AppSettingsModel {
        try await perform(try makeRequest(path: "/api/settings"), as: AppSettingsModel.self)
    }

    func updateAppSettings(_ s: AppSettingsModel) async throws -> AppSettingsModel {
        let body = try encoder.encode(s)
        return try await perform(try makeRequest(path: "/api/settings", method: "PUT", body: body), as: AppSettingsModel.self)
    }

    func logout() async {
        if let req = try? makeRequest(path: "/api/auth/logout", method: "POST") {
            _ = try? await URLSession.shared.data(for: req)
        }
        SharedConfig.clearSession()
    }
}
