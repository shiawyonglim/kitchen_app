import SwiftUI

struct ModulesHubView: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        NavigationStack {
            List {
                Section("Money & Net Worth") {
                    NavigationLink {
                        FinanceView().environmentObject(appState)
                    } label: {
                        Label("Finance 50/30/20 & AI Coach", systemImage: "brain.head.profile")
                    }
                    NavigationLink {
                        AssetsView()
                    } label: {
                        Label("Assets & Net Worth", systemImage: "shippingbox.fill")
                    }
                    NavigationLink {
                        FrictionView()
                    } label: {
                        Label("Car, Gear & Debts", systemImage: "car.fill")
                    }
                }

                Section("Health & Daily") {
                    NavigationLink {
                        KitchenView()
                    } label: {
                        Label("Kitchen & Shopping List", systemImage: "refrigerator.fill")
                    }
                    NavigationLink {
                        CaloriesView()
                    } label: {
                        Label("Calories & Food Log", systemImage: "flame.fill")
                    }
                    NavigationLink {
                        TrainerView()
                    } label: {
                        Label("Trainer & Body Measurements", systemImage: "figure.strengthtraining.traditional")
                    }
                }
            }
            .navigationTitle("Modules")
        }
    }
}
