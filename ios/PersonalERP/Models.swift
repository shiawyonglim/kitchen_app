import Foundation

struct AuthUser: Codable {
    let id: Int
    let email: String
    let full_name: String?
}

struct MobileLoginResponse: Codable {
    let access_token: String
    let token_type: String
    let expires_at: String
    let user: AuthUser
}

struct BankAccountBalance: Codable, Identifiable {
    let id: Int?
    let name: String
    let current_balance_myr: Double
    let tx_count: Int

    var stableId: String { name }
}

struct MonthlyCategoryPoint: Codable {
    let month: String
}

struct WealthSummary: Codable {
    let total_income_myr: Double
    let total_repayment_myr: Double
    let total_expense_myr: Double
    let net_cash_flow_myr: Double
    let rolling_30d_daily_burn_myr: Double
    let period_daily_burn_myr: Double
    let transaction_count: Int
    let monthly_series: [MonthlyCategoryPoint]?
    let category_breakdown: [String: Double]
    let account_balances: [BankAccountBalance]
    let remaining_fixed_bills_myr: Double
    let burn_pace_ratio: Double?
}

struct LedgerEntry: Codable, Identifiable {
    let id: Int
    let merchant: String
    let display_merchant: String?
    let category: String
    let tx_type: String
    let amount_myr: Double
    let net_amount_myr: Double?
    let occurred_at: String
    let account_name: String
    let is_self_transfer: Bool
    let is_claimable: Bool

    var effectiveMerchant: String {
        if let d = display_merchant, !d.isEmpty { return d }
        return merchant
    }

    var effectiveAmount: Double {
        net_amount_myr ?? amount_myr
    }

    var shortDate: String {
        String(occurred_at.prefix(10))
    }
}

struct CategoryRule: Codable, Identifiable {
    let id: Int
    let name: String
    let color: String?
}

struct SyncResult: Codable {
    let scanned_emails: Int
    let inserted_count: Int
    let skipped_duplicates: Int
}

struct ManualLedgerRequest: Encodable {
    let merchant: String
    let category: String
    let tx_type: String
    let amount_myr: Double
    let occurred_at: String
    let account_name: String
    let remark: String
}

struct NotificationTestResponse: Codable {
    let configured: Bool
    let sent: Bool
}

struct WidgetConfigResponse: Codable {
    let widget_api_token: String
    let ntfy_topic_url: String
}

struct AssistantItem: Codable, Identifiable {
    let id: Int
    let source: String
    let sender: String
    let raw_message: String
    let category: String
    let title: String
    let ai_plan: String
    let scheduled_for: String
    let priority: String
    let status: String
    let routed_to: String
    let engine: String
    let created_at: String
}

struct MorningBriefing: Codable {
    let date_label: String
    let greeting: String
    let pending_tasks: [AssistantItem]
    let scheduled_items: [AssistantItem]
    let expiring_kitchen: [String]
    let ai_focus_plan: String
    let engine: String
}
