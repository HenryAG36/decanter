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

struct InstallSheet: View {
    @EnvironmentObject var store: Store
    @Environment(\.dismiss) var dismiss
    let bottle: Bottle?
    @State private var installer = ""
    @State private var target: String = ""
    @State private var useNewBottle = false
    @State private var newName = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Install a Windows Application").font(.title2).bold()
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
            Picker("Install into:", selection: $target) {
                Text("New bottle…").tag("__new__")
                ForEach(store.bottles) { Text($0.name).tag($0.path) }
            }
            if target == "__new__" {
                TextField("New bottle name:", text: $newName)
            }
            Text("It is recommended that you install into a new bottle to avoid interference between programs.")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                Button("Install") { install(); dismiss() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(installer.isEmpty || (target == "__new__" && newName.isEmpty))
            }
        }
        .padding(20).frame(width: 460)
        .onAppear { if let b = bottle { target = b.path } else { target = "__new__" } }
    }

    private func install() {
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
