import Foundation

enum SharedConfig {
    static let appGroupID = "group.com.shiawyong.personalerp"
    private static let baseURLKey = "erp_base_url"
    private static let tokenKey = "erp_bearer_token"
    private static let userEmailKey = "erp_user_email"

    private static var defaults: UserDefaults {
        UserDefaults(suiteName: appGroupID) ?? .standard
    }

    static func saveSession(baseURL: String, token: String, email: String) {
        let cleaned = baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        defaults.set(cleaned, forKey: baseURLKey)
        defaults.set(token, forKey: tokenKey)
        defaults.set(email, forKey: userEmailKey)
    }

    static func clearSession() {
        defaults.removeObject(forKey: tokenKey)
        defaults.removeObject(forKey: userEmailKey)
    }

    static func loadCredentials() -> (baseURL: String, token: String, email: String)? {
        guard
            let baseURL = defaults.string(forKey: baseURLKey), !baseURL.isEmpty,
            let token = defaults.string(forKey: tokenKey), !token.isEmpty
        else {
            return nil
        }
        let email = defaults.string(forKey: userEmailKey) ?? ""
        return (baseURL, token, email)
    }

    static func lastBaseURL() -> String {
        defaults.string(forKey: baseURLKey) ?? ""
    }
}

struct WidgetRecentTx: Codable, Identifiable {
    let merchant: String
    let category: String
    let tx_type: String
    let amount_myr: Double
    let account_name: String
    let occurred_at: String

    var id: String { "\(occurred_at)-\(merchant)-\(amount_myr)" }
}

struct WidgetAccountBalance: Codable, Identifiable {
    let name: String
    let balance_myr: Double

    var id: String { name }
}

struct WidgetSummary: Codable {
    let month: String
    let today: String
    let today_expense_myr: Double
    let today_income_myr: Double
    let month_expense_myr: Double
    let month_income_myr: Double
    let monthly_budget_myr: Double
    let budget_remaining_myr: Double
    let budget_used_pct: Double
    let daily_burn_myr: Double
    let burn_pace_ratio: Double?
    let remaining_fixed_bills_myr: Double
    let accounts: [WidgetAccountBalance]
    let recent: [WidgetRecentTx]
    let updated_at: String
}

func formatRM(_ amount: Double) -> String {
    let f = NumberFormatter()
    f.numberStyle = .decimal
    f.minimumFractionDigits = 2
    f.maximumFractionDigits = 2
    return "RM \(f.string(from: NSNumber(value: amount)) ?? String(format: "%.2f", amount))"
}
