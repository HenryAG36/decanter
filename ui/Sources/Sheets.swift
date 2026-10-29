import SwiftUI
import UniformTypeIdentifiers

struct NewBottleSheet: View {
    @EnvironmentObject var store: Store
    @Environment(\.dismiss) var dismiss
    @State private var name = ""
    @State private var template = "win10_64"

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Create a Bottle").font(.title2).bold()
            Form {
                TextField("Bottle name:", text: $name)
                Picker("Bottle type:", selection: $template) {
                    ForEach(templates, id: \.self) { Text($0).tag($0) }
                }
            }
            Text("A bottle is a self-contained Windows environment holding its own C: drive, registry and installed programs.")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                Button("Create") {
                    store.cli(["create", name, "-t", template]); dismiss()
                }
                .keyboardShortcut(.defaultAction)
                .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty ||
                          store.bottles.contains { $0.name == name })
            }
        }
        .padding(20).frame(width: 420)
    }

    private var templates: [String] {
        let t = store.engines.flatMap { $0.templates ?? [] }
        return t.isEmpty ? ["win10_64", "win11_64", "win7_64", "win7_32", "winxp"]
                         : Array(Set(t)).sorted()
    }
}

struct RestoreSheet: View {
    @EnvironmentObject var store: Store
    @Environment(\.dismiss) var dismiss
    @State private var archive = ""
    @State private var name = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Restore Bottle Archive").font(.title2).bold()
            HStack {
                TextField("Archive (.tar / .tar.gz):", text: $archive)
                Button("Choose…") {
                    let p = NSOpenPanel()
                    p.allowedContentTypes = [.init(filenameExtension: "gz") ?? .data, .init(filenameExtension: "tar") ?? .data]
                    if p.runModal() == .OK { archive = p.url?.path ?? archive }
                }
            }
            TextField("New bottle name (optional):", text: $name)
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                Button("Restore") {
                    var args = ["restore", archive]
                    if !name.isEmpty { args += ["--name", name] }
                    store.cli(args); dismiss()
                }.keyboardShortcut(.defaultAction).disabled(archive.isEmpty)
            }
        }
        .padding(20).frame(width: 440)
    }
}

struct Recipe: Codable, Identifiable {
    var id: String
    var name: String
    var description: String?
    var bottle_name: String?
    var template: String?
}

struct InstallSheet: View {
    @EnvironmentObject var store: Store
    @Environment(\.dismiss) var dismiss
    let bottle: Bottle?
    @State private var mode = "recipe"
    @State private var recipes: [Recipe] = []
    @State private var recipeID = ""
    @State private var installer = ""
    @State private var target: String = ""
    @State private var newName = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Install a Windows Application").font(.title2).bold()
            Picker("", selection: $mode) {
                Text("Known application").tag("recipe")
                Text("Installer file").tag("file")
            }.pickerStyle(.segmented).labelsHidden()
            if mode == "recipe" {
                if recipes.isEmpty {
                    Text("Loading recipes…").foregroundStyle(.secondary)
                } else {
                    Picker("Application:", selection: $recipeID) {
                        ForEach(recipes) { Text($0.name).tag($0.id) }
                    }
                    if let r = recipes.first(where: { $0.id == recipeID }),
                       let d = r.description {
                        Text(d).font(.caption).foregroundStyle(.secondary)
                    }
                    HStack {
                        Text("Bottle:").foregroundStyle(.secondary)
                        TextField("name", text: $newName)
                    }
                }
            } else {
                HStack {
                    TextField("Installer file:", text: $installer)
                    Button("Choose…") {
                        let p = NSOpenPanel()
                        p.allowedContentTypes = [
                            .init(filenameExtension: "exe") ?? .data,
                            .init(filenameExtension: "msi") ?? .data,
                            .init(filenameExtension: "bat") ?? .data]
                        if p.runModal() == .OK { installer = p.url?.path ?? installer }
                    }
                }
            }
            if mode == "file" {
                Picker("Install into:", selection: $target) {
                    Text("New bottle…").tag("__new__")
                    ForEach(store.bottles) { Text($0.name).tag($0.path) }
                }
                if target == "__new__" {
                    TextField("New bottle name:", text: $newName)
                }
            }
            Text("It is recommended that you install into a new bottle to avoid interference between programs.")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                Button("Install") { install(); dismiss() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(canInstall)
            }
        }
        .padding(20).frame(width: 480)
        .onAppear {
            if let b = bottle { target = b.path } else { target = "__new__" }
            Task.detached {
                let rs = (try? DecanterCLI.decode([Recipe].self, ["install", "--list"])) ?? []
                await MainActor.run {
                    recipes = rs
                    recipeID = rs.first?.id ?? ""
                    if newName.isEmpty { newName = rs.first?.bottle_name ?? "" }
                }
            }
        }
        .onChange(of: recipeID) { _, v in
            if let r = recipes.first(where: { $0.id == v }) {
                newName = r.bottle_name ?? r.name
            }
        }
    }

    private var canInstall: Bool {
        if mode == "recipe" {
            return recipeID.isEmpty || newName.trimmingCharacters(in: .whitespaces).isEmpty
        }
        return installer.isEmpty || (target == "__new__" && newName.isEmpty)
    }

    private func install() {
        if mode == "recipe" {
            store.cli(["install", recipeID, "-b", newName])
            return
        }
        if target == "__new__" {
            store.cli(["create", newName]) { _ in
                DispatchQueue.main.async {
                    store.launch(["-b", newName, "--", installer])
                }
            }
        } else {
            store.launch(["-b", target, "--", installer])
        }
    }
}

struct RunCommandSheet: View {
    @EnvironmentObject var store: Store
    @Environment(\.dismiss) var dismiss
    let bottle: Bottle?
    @State private var command = ""
    @State private var target = ""
    @State private var options = ""
    @State private var capture = false
    @State private var result: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Run Command…").font(.title2).bold()
            Picker("Bottle:", selection: $target) {
                ForEach(store.bottles) { Text($0.name).tag($0.path) }
            }
            HStack {
                TextField("Command or file:", text: $command)
                Button("Choose…") {
                    let p = NSOpenPanel()
                    if p.runModal() == .OK { command = p.url?.path ?? command }
                }
            }
            TextField("Options (e.g. --desktop game,1280x720 --dll xaudio2_7=native)", text: $options)
                .font(.caption)
            Toggle("Capture command output", isOn: $capture)
            if let r = result {
                GroupBox("Command results") {
                    ScrollView {
                        Text(r).font(.system(.caption, design: .monospaced))
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }.frame(maxHeight: 200)
                }
            }
            HStack {
                Spacer()
                Button("Close") { dismiss() }
                Button("Run") { run() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(command.isEmpty || target.isEmpty)
            }
        }
        .padding(20).frame(width: 500)
        .onAppear { target = bottle?.path ?? store.bottles.first?.path ?? "" }
    }

    private func run() {
        let opts = options.split(separator: " ").map(String.init)
        let args = ["run", "-b", target] + opts + ["--", command]
        if capture {
            Task.detached {
                var res: CmdResult
                do { res = try DecanterCLI.run(args, json: false) }
                catch { res = CmdResult(status: -1, stdout: "", stderr: "\(error)") }
                await MainActor.run {
                    result = "exit \(res.status)\n" +
                        (res.stdout.isEmpty ? "" : "output:\n\(res.stdout)\n") +
                        (res.stderr.isEmpty ? "" : "error text:\n\(res.stderr)")
                }
            }
        } else {
            store.cli(args, refreshAfter: false)
        }
    }
}

struct GPTKSheet: View {
    @EnvironmentObject var store: Store
    @Environment(\.dismiss) var dismiss
    @State private var dmg = ""
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("D3DMetal needs Apple's own download").font(.title2).bold()
            Text("""
            D3DMetal — the fastest DX11/DX12 path, and the only good DX12 \
            option — is Apple's proprietary component in the free Game \
            Porting Toolkit. Decanter can't download it for you (Apple's \
            license), but it's a one-time download with a free Apple \
            Developer account.
            """)
            .font(.callout).foregroundStyle(.secondary)
            HStack {
                Button("Open Apple's Download Page") {
                    NSWorkspace.shared.open(URL(string:
                        "https://developer.apple.com/download/all/?q=game%20porting%20toolkit")!)
                }
                Spacer()
            }
            Divider()
            HStack {
                TextField("Downloaded Game-Porting-Toolkit .dmg:", text: $dmg)
                Button("Choose…") {
                    let p = NSOpenPanel()
                    p.allowedContentTypes = [.init(filenameExtension: "dmg") ?? .data]
                    if p.runModal() == .OK { dmg = p.url?.path ?? dmg }
                }
            }
            Text("Once installed, the D3DMetal backend becomes available per bottle.")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                Button("Install from .dmg") {
                    store.cli(["engine", "setup-d3dmetal", "--dmg", dmg]); dismiss()
                }.keyboardShortcut(.defaultAction).disabled(dmg.isEmpty)
            }
        }
        .padding(20).frame(width: 480)
    }
}

struct MigrateSheet: View {
    @EnvironmentObject var store: Store
    @Environment(\.dismiss) var dismiss
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Migrate from CrossOver").font(.title2).bold()
            Text("""
            This moves your bottles into Decanter's own folder so nothing is \
            lost when CrossOver and its support files are removed, installs a \
            free open-source Wine engine if needed, and adjusts settings your \
            old engine supported but the new one doesn't (e.g. D3DMetal → auto, \
            MSync → ESync).

            Nothing is deleted — CrossOver stays installed and your bottles \
            keep working in Decanter.
            """)
            .font(.callout).foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                Button("Migrate") {
                    store.cli(["migrate"]); dismiss()
                }.keyboardShortcut(.defaultAction)
            }
        }
        .padding(20).frame(width: 440)
    }
}

// MARK: - Preferences

struct PreferencesView: View {
    @EnvironmentObject var store: Store
    var body: some View {
        TabView {
            Form {
                if let d = store.doctor {
                    LabeledContent("Decanter", value: d.decanter_version)
                    LabeledContent("Machine", value: "\(d.machine) · macOS \(d.macos)")
                    LabeledContent("Rosetta 2") {
                        Text(d.rosetta ? "Installed" : "Missing — required for x86 programs")
                            .foregroundStyle(d.rosetta ? Color.primary : Color.orange)
                    }
                    LabeledContent("Bottles", value: "\(d.bottle_count)")
                }
                LabeledContent("Bottles folder") {
                    Text("~/Library/Application Support/CrossOver/Bottles + ~/.decanter/bottles")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Button("Refresh") { store.refresh() }
            }
            .padding()
            .tabItem { Label("General", systemImage: "gear") }

            Form {
                ForEach(store.engines) { e in
                    VStack(alignment: .leading, spacing: 3) {
                        HStack {
                            Text(e.name).bold()
                            if e.default == true { Text("default").font(.caption)
                                .padding(.horizontal, 6).padding(.vertical, 1)
                                .background(.blue.opacity(0.15), in: .capsule) }
                        }
                        Text("\(e.kind) · \(e.version ?? "?")").font(.caption)
                        Text(e.root).font(.caption2).foregroundStyle(.secondary)
                        if let b = e.backends {
                            Text("backends: \(b.joined(separator: ", "))")
                                .font(.caption2).foregroundStyle(.secondary)
                        }
                    }
                    .padding(.vertical, 4)
                }
                Text("Install more engines with `decanter engine install`.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            .padding()
            .tabItem { Label("Engines", systemImage: "cpu") }

            Form {
                ScrollView {
                    Text(store.outputLog.isEmpty ? "(no commands yet)" : store.outputLog)
                        .font(.system(.caption, design: .monospaced))
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                Button("Clear") { store.outputLog = "" }
            }
            .padding()
            .tabItem { Label("Diagnostics", systemImage: "stethoscope") }
        }
        .frame(width: 520, height: 380)
    }
}
