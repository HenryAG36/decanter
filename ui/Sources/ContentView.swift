import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    @EnvironmentObject var store: Store
    @AppStorage("selection") private var selection = "home"
    @State private var sheet: Sheet?
    @State private var confirmDelete: Bottle?
    @State private var renameTarget: Bottle?
    @State private var renameText = ""
    @State private var rebootAlert: (Bottle, String, () -> Void)?
    @State private var showLog = false

    enum Sheet: Identifiable {
        case newBottle, install(Bottle?), runCmd(Bottle?), restore
        var id: String {
            switch self {
            case .newBottle: return "new"; case .restore: return "restore"
            case .install(let b): return "install-\(b?.id ?? "none")"
            case .runCmd(let b): return "run-\(b?.id ?? "none")"
            }
        }
    }

    var selectedBottle: Bottle? { store.bottles.first { $0.id == selection } }

    var body: some View {
        NavigationSplitView {
            List(selection: $selection) {
                Section {
                    Label("Home", systemImage: "house").tag("home")
                }
                Section("Bottles") {
                    ForEach(store.bottles) { b in
                        HStack {
                            Image(systemName: "wineglass")
                            VStack(alignment: .leading) {
                                Text(b.name)
                                Text(b.subtitle).font(.caption).foregroundStyle(.secondary)
                            }
                            if b.default == true { Spacer(); Image(systemName: "star.fill")
                                .foregroundStyle(.yellow).font(.caption) }
                        }
                        .tag(b.id)
                        .contextMenu { bottleMenu(b) }
                    }
                }
            }
            .navigationSplitViewColumnWidth(min: 190, ideal: 210)
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Menu {
                        Button("New Bottle…") { sheet = .newBottle }
                        Button("Restore Bottle Archive…") { sheet = .restore }
                    } label: { Image(systemName: "plus") }
                }
            }
        } detail: {
            if let b = selectedBottle {
                BottleDetailView(bottle: b, sheet: $sheet, confirmDelete: $confirmDelete,
                                 renameTarget: $renameTarget, renameText: $renameText,
                                 rebootAlert: $rebootAlert)
            } else {
                HomeView(sheet: $sheet)
            }
        }
        .toolbar {
            ToolbarItem(placement: .automatic) {
                if store.busy { ProgressView().controlSize(.small) }
            }
            ToolbarItem(placement: .automatic) {
                Button { showLog.toggle() } label: {
                    Image(systemName: "terminal") }
                    .help("Command log")
            }
        }
        .overlay(alignment: .bottom) {
            if showLog { LogView(text: store.outputLog, close: { showLog = false })
                .frame(height: 180).transition(.move(edge: .bottom)) }
        }
        .sheet(item: $sheet) { s in
            switch s {
            case .newBottle: NewBottleSheet().environmentObject(store)
            case .restore: RestoreSheet().environmentObject(store)
            case .install(let b): InstallSheet(bottle: b ?? selectedBottle)
                .environmentObject(store)
            case .runCmd(let b): RunCommandSheet(bottle: b ?? selectedBottle)
                .environmentObject(store)
            }
        }
        .alert("Delete bottle?", isPresented: .constant(confirmDelete != nil)) {
            Button("Delete", role: .destructive) {
                if let b = confirmDelete {
                    store.cli(["delete", b.name, "--force"])
                }
                confirmDelete = nil
            }
            Button("Cancel", role: .cancel) { confirmDelete = nil }
        } message: {
            Text("Deleting '\(confirmDelete?.name ?? "")' permanently erases all Windows applications and documents in its C: drive.")
        }
        .alert("Decanter", isPresented: Binding(
            get: { store.lastError != nil },
            set: { if !$0 { store.lastError = nil } })) {
            Button("OK") { store.lastError = nil }
        } message: { Text(store.lastError ?? "") }
        .alert("Rename Bottle", isPresented: .constant(renameTarget != nil)) {
            TextField("New name", text: $renameText)
            Button("Rename") {
                if let b = renameTarget, !renameText.isEmpty {
                    store.cli(["rename", b.name, renameText])
                }
                renameTarget = nil
            }
            Button("Cancel", role: .cancel) { renameTarget = nil }
        } message: { Text("Enter a new name for '\(renameTarget?.name ?? "")'.") }
        .alert("Reboot required", isPresented: .constant(rebootAlert != nil)) {
            Button("Apply & Reboot Bottle") {
                if let (b, _, action) = rebootAlert {
                    action(); store.cli(["wineboot", "-b", b.path])
                }
                rebootAlert = nil
            }
            Button("Cancel", role: .cancel) { rebootAlert = nil }
        } message: {
            Text("Changing this setting reboots the bottle; running apps will quit. \(rebootAlert?.1 ?? "")")
        }
        .onReceive(NotificationCenter.default.publisher(for: .newBottle)) { _ in sheet = .newBottle }
        .onReceive(NotificationCenter.default.publisher(for: .installApp)) { _ in sheet = .install(selectedBottle) }
        .onReceive(NotificationCenter.default.publisher(for: .runCommand)) { _ in sheet = .runCmd(selectedBottle) }
        .onReceive(NotificationCenter.default.publisher(for: .openCDrive)) { _ in
            if let b = selectedBottle {
                NSWorkspace.shared.open(URL(fileURLWithPath: b.path + "/drive_c"))
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .forceQuit)) { _ in
            if let b = selectedBottle { store.cli(["kill", "-b", b.path]) }
        }
    }

    @ViewBuilder
    private func bottleMenu(_ b: Bottle) -> some View {
        Button("Open C: Drive") {
            NSWorkspace.shared.open(URL(fileURLWithPath: b.path + "/drive_c"))
        }
        Button("Reveal in Finder") {
            NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: b.path)])
        }
        Divider()
        Button(b.default == true ? "Default Bottle" : "Make Default") {
            store.cli(["default", b.name])
        }
        Button("Rename…") { renameText = b.name; renameTarget = b }
        Button("Duplicate…") {
            store.cli(["copy", b.name, "Copy of \(b.name)"])
        }
        Button("Export to Archive…") { export(b) }
        Divider()
        Button("Force Quit Applications") { store.cli(["kill", "-b", b.path]) }
        Button("Rebuild Program List") { store.refresh() }
        Divider()
        Button("Delete…", role: .destructive) { confirmDelete = b }
    }

    private func export(_ b: Bottle) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "\(b.name).tar.gz"
        panel.allowedContentTypes = [.init(filenameExtension: "gz") ?? .data]
        if panel.runModal() == .OK, let url = panel.url {
            store.cli(["archive", b.name, "-o", url.path])
        }
    }
}

// MARK: - Home

struct HomeView: View {
    @EnvironmentObject var store: Store
    @Binding var sheet: ContentView.Sheet?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                if let d = store.doctor, !d.rosetta {
                    Label("Rosetta 2 is not installed — required for x86 Windows programs.",
                          systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(.orange).padding(10)
                        .background(.orange.opacity(0.12), in: .rect(cornerRadius: 8))
                }
                HStack {
                    Text("Installed Programs").font(.title2).bold()
                    Spacer()
                    Button("Install Software…") { sheet = .install(nil) }
                        .controlSize(.large)
                    Button("New Bottle…") { sheet = .newBottle }
                }
                let all = store.allPrograms()
                if all.isEmpty {
                    ContentUnavailableView("No programs found",
                        systemImage: "square.grid.2x2",
                        description: Text("Install a Windows application or create a bottle to get started."))
                } else {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 96))], spacing: 18) {
                        ForEach(all, id: \.1.id) { b, p in ProgramTile(bottle: b, program: p) }
                    }
                }
                if let err = store.lastError {
                    Text(err).font(.callout).foregroundStyle(.red)
                }
            }
            .padding(24)
        }
    }
}

// MARK: - Bottle detail

struct BottleDetailView: View {
    @EnvironmentObject var store: Store
    let bottle: Bottle
    @Binding var sheet: ContentView.Sheet?
    @Binding var confirmDelete: Bottle?
    @Binding var renameTarget: Bottle?
    @Binding var renameText: String
    @Binding var rebootAlert: (Bottle, String, () -> Void)?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                header
                Divider()
                programs
                Divider()
                settings
                Divider()
                tools
            }
            .padding(24)
        }
        .navigationTitle(bottle.name)
    }

    private var header: some View {
        HStack(alignment: .center) {
            Image(systemName: "wineglass").font(.system(size: 34))
            VStack(alignment: .leading, spacing: 2) {
                Text(bottle.name).font(.title).bold()
                Text(bottle.path).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Button("Install Software…") { sheet = .install(bottle) }
            Button("Run Command…") { sheet = .runCmd(bottle) }
            Menu("Manage") {
                Button("Open C: Drive") {
                    NSWorkspace.shared.open(URL(fileURLWithPath: bottle.path + "/drive_c"))
                }
                Divider()
                Button("Rename…") { renameText = bottle.name; renameTarget = bottle }
                Button("Duplicate…") {
                    store.cli(["copy", bottle.name, "Copy of \(bottle.name)"])
                }
                Button("Set as Default") { store.cli(["default", bottle.name]) }
                Divider()
                Button("Force Quit Applications") {
                    store.cli(["kill", "-b", bottle.path])
                }
                Button("Delete…", role: .destructive) { confirmDelete = bottle }
            }
        }
    }

    private var programs: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Programs").font(.headline)
            let progs = store.programsFor(bottle)
            if progs.isEmpty {
                Text("No software is installed")
                    .foregroundStyle(.secondary).font(.callout)
            } else {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 96))], spacing: 18) {
                    ForEach(progs) { p in ProgramTile(bottle: bottle, program: p) }
                }
            }
        }
    }

    private var settings: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Bottle Settings").font(.headline)
            Grid(alignment: .leading, horizontalSpacing: 20, verticalSpacing: 10) {
                GridRow {
                    Text("Graphics backend").foregroundStyle(.secondary)
                    Picker("", selection: backendBinding) {
                        Text("Auto").tag("auto")
                        Text("D3DMetal").tag("d3dmetal")
                        Text("DXMT").tag("dxmt")
                        Text("DXVK").tag("dxvk")
                        Text("Wine (wined3d)").tag("wined3d")
                    }.labelsHidden().frame(width: 160)
                    Text("Translation layer for DirectX rendering")
                        .font(.caption).foregroundStyle(.secondary)
                }
                GridRow {
                    Text("Windows version").foregroundStyle(.secondary)
                    Picker("", selection: winverBinding) {
                        ForEach(["win11", "win10", "win81", "win7"], id: \.self) {
                            Text($0.replacingOccurrences(of: "win", with: "Windows ")).tag($0)
                        }
                    }.labelsHidden().frame(width: 160)
                    Text("Version reported to programs").font(.caption).foregroundStyle(.secondary)
                }
            }
            Toggle("MSync — Mach-semaphore synchronization", isOn: toggleBinding(
                get: bottle.msync, key: "msync", warn: "Some Windows applications will not work correctly with MSync enabled."))
            Toggle("High resolution mode (Retina rendering)", isOn: toggleBinding(
                get: bottle.retina, key: "retina", warn: "Many Windows applications render incorrectly in high resolution mode."))
            Toggle("NVAPI / DLSS support (DXVK)", isOn: Binding(
                get: { bottle.nvapi },
                set: { on in store.setConfig(bottle,
                    ["env:DXVK_ENABLE_NVAPI=\(on ? "1" : "")", "override:nvapi64=\(on ? "native,builtin" : "disabled")"]) }))
            if bottle.esync || bottle.fsync {
                Toggle("Esync", isOn: toggleBinding(get: bottle.esync, key: "esync", warn: ""))
                Toggle("Fsync", isOn: toggleBinding(get: bottle.fsync, key: "fsync", warn: ""))
            }
        }
    }

    private var tools: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Tools").font(.headline)
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), alignment: .leading)], spacing: 8) {
                ForEach([("Wine Configuration", "winecfg", "gearshape"),
                         ("Registry Editor", "regedit", "list.bullet.rectangle"),
                         ("Control Panel", "control", "switch.2"),
                         ("Task Manager", "taskmgr", "chart.bar"),
                         ("Explorer", "explorer", "folder"),
                         ("Command Prompt", "cmd", "apple.terminal"),
                         ("Uninstaller", "uninstaller", "trash"),
                         ("Notepad", "notepad", "note.text")], id: \.1) { name, cmd, icon in
                    Button {
                        store.launch(["-b", bottle.path, "--", toolExe(cmd)])
                    } label: {
                        Label(name, systemImage: icon).frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
        }
    }

    private func toolExe(_ t: String) -> String {
        switch t {
        case "winecfg": return "winecfg.exe"; case "regedit": return "regedit.exe"
        case "control": return "control.exe"; case "taskmgr": return "taskmgr.exe"
        case "explorer": return "explorer.exe"; case "cmd": return "cmd.exe"
        case "uninstaller": return "uninstaller.exe"; default: return "notepad.exe"
        }
    }

    private var backendBinding: Binding<String> {
        Binding(get: { bottle.backend }, set: { v in
            if v != bottle.backend {
                rebootAlert = (bottle, "", { store.setBackend(bottle, v) })
            }
        })
    }
    private var winverBinding: Binding<String> {
        Binding(get: { "win10" }, set: { store.setConfig(bottle, ["winver=\($0)"]) })
    }
    private func toggleBinding(get: Bool, key: String, warn: String) -> Binding<Bool> {
        Binding(get: { get }, set: { on in
            if warn.isEmpty { store.setToggle(bottle, key: key, on: on) }
            else { rebootAlert = (bottle, warn, { store.setToggle(bottle, key: key, on: on) }) }
        })
    }
}

// MARK: - Program tile

struct ProgramTile: View {
    @EnvironmentObject var store: Store
    let bottle: Bottle
    let program: Program

    var body: some View {
        VStack(spacing: 6) {
            Image(systemName: program.isURL ? "link" : "app")
                .font(.system(size: 32)).frame(width: 52, height: 52)
                .background(.quaternary, in: .rect(cornerRadius: 12))
            Text(program.name).font(.caption).lineLimit(2)
                .multilineTextAlignment(.center).frame(width: 92)
        }
        .contentShape(Rectangle())
        .onTapGesture(count: 2) { run() }
        .contextMenu {
            Button("Run") { run() }
            if let t = program.target {
                Button("Create Shortcut App…") { shortcut(t) }
            }
            if let lf = program.link_file {
                Button("Show Link File") {
                    NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: lf)])
                }
            }
        }
        .help(program.target ?? "No launch target found")
    }

    private func run() {
        guard let t = program.target else { return }
        if program.isURL {
            store.cli(["start", "-b", bottle.path, t], refreshAfter: false)
        } else {
            store.launch(["-b", bottle.path, "--", t])
        }
    }

    private func shortcut(_ t: String) {
        store.cli(["shortcut", "-b", bottle.path, "--exe", t,
                   "--name", program.name])
    }
}

// MARK: - Log

struct LogView: View {
    let text: String
    var close: () -> Void
    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text("Command results").font(.headline)
                Spacer(); Button(action: close) { Image(systemName: "xmark") }
            }.padding(8).background(.bar)
            ScrollView {
                Text(text).font(.system(.caption, design: .monospaced))
                    .frame(maxWidth: .infinity, alignment: .leading).padding(8)
            }.background(Color(nsColor: .textBackgroundColor))
        }
    }
}
