import SwiftUI

@main
struct DecanterApp: App {
    @StateObject private var store = Store()
    @AppStorage("selectedBottle") private var selected: String = ""

    var body: some Scene {
        WindowGroup("Decanter", id: "main") {
            ContentView()
                .environmentObject(store)
                .frame(minWidth: 820, minHeight: 520)
                .tint(Color(red: 0.62, green: 0.13, blue: 0.20))
                .onAppear { store.refresh(); store.startRunningPoll() }
        }
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("New Bottle…") {
                    NotificationCenter.default.post(name: .newBottle, object: nil)
                }.keyboardShortcut("n", modifiers: .command)
            }
            CommandMenu("Bottle") {
                Button("Install Software…") {
                    NotificationCenter.default.post(name: .installApp, object: nil)
                }.keyboardShortcut("i", modifiers: .command)
                Button("Run Command…") {
                    NotificationCenter.default.post(name: .runCommand, object: nil)
                }.keyboardShortcut("r", modifiers: .command)
                Divider()
                Button("Open C: Drive") {
                    NotificationCenter.default.post(name: .openCDrive, object: nil)
                }
                Divider()
                Button("Force Quit All Applications") {
                    NotificationCenter.default.post(name: .forceQuit, object: nil)
                }
            }
        }
        Settings { PreferencesView().environmentObject(store) }
    }
}

extension Notification.Name {
    static let newBottle = Notification.Name("decanter.newBottle")
    static let installApp = Notification.Name("decanter.installApp")
    static let runCommand = Notification.Name("decanter.runCommand")
    static let openCDrive = Notification.Name("decanter.openCDrive")
    static let forceQuit = Notification.Name("decanter.forceQuit")
    static let gptkSheet = Notification.Name("decanter.gptkSheet")
}
