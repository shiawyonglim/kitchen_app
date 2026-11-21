import SwiftUI

struct LoginView: View {
    @EnvironmentObject private var appState: AppState
    @State private var baseURL: String = {
        let saved = SharedConfig.lastBaseURL()
        if !saved.isEmpty { return saved }
        return (Bundle.main.object(forInfoDictionaryKey: "ERPWidgetBaseURL") as? String) ?? ""
    }()
    @State private var email: String = ""
    @State private var password: String = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("Server Connection") {
                    TextField("https://your-pc.tailnet.ts.net", text: $baseURL)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }

                Section("Account") {
                    TextField("Email", text: $email)
                        .keyboardType(.emailAddress)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    SecureField("Password", text: $password)
                }

                if let err = appState.errorMessage {
                    Section {
                        Text(err)
                            .foregroundStyle(.red)
                            .font(.footnote)
                    }
                }

                Section {
                    Button {
                        Task {
                            await appState.signIn(baseURL: baseURL, email: email, password: password)
                        }
                    } label: {
                        HStack {
                            Spacer()
                            if appState.isLoading {
                                ProgressView()
                            } else {
                                Text("Sign In")
                                    .fontWeight(.semibold)
                            }
                            Spacer()
                        }
                    }
                    .disabled(baseURL.isEmpty || email.isEmpty || password.isEmpty || appState.isLoading)
                }
            }
            .navigationTitle("Personal ERP")
        }
    }
}
