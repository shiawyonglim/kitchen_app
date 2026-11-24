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

// MARK: - Finance & 50/30/20
struct BucketDetail: Codable {
    let actual_myr: Double
    let actual_pct: Double
    let target_pct: Int
}

struct Buckets503020: Codable {
    let needs: BucketDetail
    let wants: BucketDetail
    let savings_debt: BucketDetail
}

struct SpendingLeak: Codable, Identifiable {
    let title: String
    let amount_myr: Double
    let severity: String
    let tip: String

    var id: String { title }
}

struct FinanceOverview: Codable {
    let month: String
    let budget_myr: Double
    let savings_target_myr: Double
    let income_myr: Double
    let expense_myr: Double
    let net_cash_flow_myr: Double
    let savings_rate_pct: Double
    let status: String
    let daily_burn_myr: Double
    let safe_to_spend_daily_myr: Double
    let liquid_cash_myr: Double
    let emergency_months: Double
    let buckets: Buckets503020
    let leaks: [SpendingLeak]
}

struct FinanceCoachResponse: Codable {
    let month: String
    let advice: String
    let model: String
}

// MARK: - Assets
struct AssetValuation: Codable {
    let median_price_myr: Double
    let sample_count: Int
    let source: String
}

struct TrackedAsset: Codable, Identifiable {
    let id: Int
    let name: String
    let category: String
    let condition: String
    let purchase_price_myr: Double
    let quantity: Int
    let latest_valuation: AssetValuation?
    let total_liquid_value_myr: Double
    let unrealized_pnl_myr: Double
}

struct NetWorthSummary: Codable {
    let liquid_cash_myr: Double
    let tracked_assets_value_myr: Double
    let total_net_worth_myr: Double
    let asset_count: Int
}

// MARK: - Kitchen
struct KitchenItem: Codable, Identifiable {
    let id: Int
    let name: String
    let category: String
    let quantity: Double
    let unit: String
    let days_remaining: Int
    let is_expiring_soon: Bool
    let is_staple: Bool
}

struct DinnerRecipe: Codable {
    let title: String
    let used_expiring_items: [String]
    let instructions: String
    let engine: String
    let calories: Int
    let protein_g: Int
}

struct ShoppingListItem: Codable, Identifiable {
    let id: Int
    let name: String
    let reason: String
    let is_checked: Bool
}

// MARK: - Calories
struct CalorieSummary: Codable {
    let month: String
    let daily_target_calories: Int
    let daily_target_protein_g: Int
    let goal_mode: String
    let today_calories: Int
    let today_protein_g: Double
    let today_carbs_g: Double
    let today_fat_g: Double
}

struct CalorieEntry: Codable, Identifiable {
    let id: Int
    let food_name: String
    let grams: Double
    let calories: Int
    let protein_g: Double
    let carbs_g: Double
    let fat_g: Double
    let meal_type: String
    let eaten_at: String
}

struct MacroEstimate: Codable {
    let kcal_per_100g: Double
    let protein_per_100g: Double
    let carbs_per_100g: Double
    let fat_per_100g: Double
}

struct CalorieParseResponse: Codable {
    let status: String
    let entry: CalorieEntry?
    let food_name: String
    let grams: Double
    let meal_type: String
    let suggested_per_100g: MacroEstimate?
    let message: String
}

// MARK: - Trainer
struct WorkoutLog: Codable, Identifiable {
    let id: Int
    let exercise_type: String
    let reps: Int
    let distance_km: Double
    let duration_min: Double
    let calories_burned: Int
    let logged_at: String
}

struct TrainerGoal: Codable {
    let goal_mode: String
    let target_calories: Int
    let target_protein_g: Int
}

struct TrainerAnalytics: Codable {
    let total_sessions: Int
    let total_calories_burned: Int
    let total_duration_min: Double
    let total_distance_km: Double
    let goal: TrainerGoal
}

struct BodyMeasurement: Codable, Identifiable {
    let id: Int
    let weight_kg: Double?
    let waist_cm: Double?
    let logged_at: String
}

// MARK: - Friction (Car & Debts)
struct AltisSummary: Codable {
    let vehicle: String
    let total_fuel_myr: Double
    let distance_km: Double
    let cost_per_km_myr: Double?
}

struct GearItem: Codable, Identifiable {
    let id: Int
    let name: String
    let unit: String
    let used_units: Double
    let max_units: Double
    let remaining_units: Double
    let health_pct: Double
    let alert: Bool
}

struct SocialDebt: Codable, Identifiable {
    let id: Int
    let event_name: String
    let borrower_name: String
    let amount_myr: Double
    let direction: String
    let paid_myr: Double
    let remaining_myr: Double
    let settled: Bool
}

struct FrictionSummary: Codable {
    let altis: AltisSummary
    let gear: [GearItem]
    let unsettled_debts: [SocialDebt]
    let total_owed_myr: Double
    let total_i_owe_myr: Double
}

// MARK: - App Settings
struct AppSettingsModel: Codable {
    var auto_sync_enabled: Bool
    var live_listener_enabled: Bool
    var auto_learn_categories: Bool
    var auto_sync_hour: Int
    var auto_sync_lookback_days: Int
    var monthly_budget_myr: Double
    var monthly_savings_target_myr: Double
    var default_calories: Int
    var default_protein_g: Int
    var account_holder_names: String
}
