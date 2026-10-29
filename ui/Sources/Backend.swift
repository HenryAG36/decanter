import SwiftUI
import Combine

// MARK: - Models (decode `decanter --json` output)

struct Bottle: Identifiable, Codable, Hashable {
    var id: String { path }
    let name: String
    let path: String
    let template: String?
    let winearch: String?
    let version: String?
    let env: [String: String]?
    let `default`: Bool?

    var backend: String { env?["CX_GRAPHICS_BACKEND"] ?? "auto" }
    var msync: Bool { env?["WINEMSYNC"] == "1" }
    var esync: Bool { env?["WINEESYNC"] == "1" }
    var fsync: Bool { env?["WINEFSYNC"] == "1" }
    var nvapi: Bool { env?["DXVK_ENABLE_NVAPI"] == "1" }
    var retina: Bool { env?["CX_BOTTLE_RETINA"] == "1" }
    var subtitle: String {
        [template, winearch].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")
    }

    /// Stable per-bottle tint for sidebar badges.
    var badgeColor: Color {
        let palette: [Color] = [
            Color(red: 0.62, green: 0.13, blue: 0.20), .indigo, .teal,
            .brown, .purple, .orange, .cyan]
        return palette[abs(name.hashValue) % palette.count]
    }
}

struct Program: Identifiable, Codable, Hashable {
    var id: String { name }
    let name: String
    let target: String?
    let source: String?
    let link_file: String?
    let icon: String?

    var isURL: Bool {
        guard let t = target?.lowercased() else { return false }
        return t.contains("://")
    }
}

struct EngineInfo: Identifiable, Codable, Hashable {
    var id: String { name }
    let name: String
    let kind: String
    let version: String?
    let root: String
    let backends: [String]?
    let templates: [String]?
    let `default`: Bool?
}

struct DoctorInfo: Codable {
    let decanter_version: String
    let machine: String
    let macos: String
    let rosetta: Bool
    let engines: [EngineBrief]
    let bottle_count: Int
    struct EngineBrief: Codable { let name: String; let kind: String
        let version: String?; let backends: [String]?; let root: String }
}

struct BottleConfig: Codable {
    let name: String
    let path: String
    let template: String?
    let arch: String?
    let windows_version: String?
    let env: [String: String]?
}

struct CmdResult: Sendable {
    var status: Int32
    var stdout: String
    var stderr: String
}

enum BackendError: Error, LocalizedError {
    case notFound, failed(String)
    var errorDescription: String? {
        switch self {
        case .notFound: return "Decanter CLI not found. Install it or set DECANTER_BIN."
        case .failed(let m): return m
        }
    }
}

// MARK: - Process runner

enum DecanterCLI {
    /// Locate the decanter backend: env override → bundled python package → PATH → dev checkout.
    static func resolve() -> (bin: String, isPackage: Bool)? {
        if let env = ProcessInfo.processInfo.environment["DECANTER_BIN"], !env.isEmpty {
            return (env, false)
        }
        if let pkg = Bundle.main.resourceURL?.appendingPathComponent("python"),
           FileManager.default.fileExists(atPath: pkg.appendingPathComponent("decanter/cli.py").path) {
            return (pkg.path, true)
        }
        for cand in ["/usr/local/bin/decanter", "/opt/homebrew/bin/decanter",
                     NSHomeDirectory() + "/.local/bin/decanter",
                     NSHomeDirectory() + "/Documents/antigravity/decanter/bin/decanter"] {
            if FileManager.default.isExecutableFile(atPath: cand) { return (cand, false) }
        }
        if let found = which("decanter") { return (found, false) }
        return nil
    }

    private static func which(_ name: String) -> String? {
        let p = Process(); p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        p.arguments = ["which", name]
        let pipe = Pipe(); p.standardOutput = pipe; p.standardError = FileHandle.nullDevice
        try? p.run(); p.waitUntilExit()
        let out = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return (p.terminationStatus == 0 && !out.isEmpty) ? out : nil
    }

    @discardableResult
    static func run(_ args: [String], json: Bool = true, wait: Bool = true) throws -> CmdResult {
        guard let resolved = resolve() else { throw BackendError.notFound }
        let p = Process()
        var argv = args
        var env = ProcessInfo.processInfo.environment
        if resolved.isPackage {
            p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
            argv = ["-m", "decanter"] + argv
            env["PYTHONPATH"] = resolved.bin
        } else {
            p.executableURL = URL(fileURLWithPath: resolved.bin)
        }
        if json { argv.insert("--json", at: resolved.isPackage ? 2 : 0) }
        p.arguments = argv
        p.environment = env
        let outPipe = Pipe(), errPipe = Pipe()
        p.standardOutput = outPipe; p.standardError = errPipe
        try p.run()
        if wait { p.waitUntilExit() }
        let r = CmdResult(
            status: wait ? p.terminationStatus : 0,
            stdout: String(data: outPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? "",
            stderr: String(data: errPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? "")
        return r
    }

    static func decode<T: Decodable>(_ t: T.Type, _ args: [String]) throws -> T {
        let r = try run(args)
        guard r.status == 0 else { throw BackendError.failed(r.stderr.isEmpty ? r.stdout : r.stderr) }
        guard let d = r.stdout.data(using: .utf8) else { throw BackendError.failed("no output") }
        return try JSONDecoder().decode(t, from: d)
    }
}

// MARK: - Store

@MainActor
final class Store: ObservableObject {
    @Published var bottles: [Bottle] = []
    @Published var engines: [EngineInfo] = []
    @Published var doctor: DoctorInfo?
    @Published var programs: [String: [Program]] = [:]   // bottle.path -> programs
    @Published var busy = false
    @Published var lastError: String?
    @Published var outputLog: String = ""

    func refresh() {
        busy = true
        Task.detached {
            var err: String? = nil
            let bottles = (try? DecanterCLI.decode([Bottle].self, ["list"])) ?? []
            let engines = (try? DecanterCLI.decode([EngineInfo].self, ["engines", "list"])) ?? []
            let doctor = try? DecanterCLI.decode(DoctorInfo.self, ["doctor"])
            if bottles.isEmpty, let r = try? DecanterCLI.run(["list"]) {
                if r.status != 0 { err = r.stderr }
            }
            var progs: [String: [Program]] = [:]
            for b in bottles {
                progs[b.path] = (try? DecanterCLI.decode([Program].self,
                                        ["programs", "-b", b.path])) ?? []
            }
            await MainActor.run {
                self.bottles = bottles; self.engines = engines
                self.doctor = doctor; self.programs = progs
                self.busy = false; self.lastError = err
            }
        }
    }

    /// Fire-and-forget action; appends output to the log and refreshes.
    func cli(_ args: [String], refreshAfter: Bool = true,
             completion: ((CmdResult) -> Void)? = nil) {
        busy = true
        Task.detached {
            var result: CmdResult
            do { result = try DecanterCLI.run(args) }
            catch { result = CmdResult(status: -1, stdout: "", stderr: "\(error)") }
            await MainActor.run {
                let text = (result.stdout + result.stderr).trimmingCharacters(in: .whitespacesAndNewlines)
                if !text.isEmpty {
                    self.outputLog += "$ decanter \(args.joined(separator: " "))\n\(text)\n\n"
                }
                if result.status != 0 {
                    self.lastError = text.isEmpty
                        ? "command failed (exit \(result.status))" : text
                }
                self.busy = false
                completion?(result)
                if refreshAfter { self.refresh() }
            }
        }
    }

    /// Launch without waiting — for GUI apps.
    func launch(_ args: [String]) { cli(["run", "--no-wait"] + args, refreshAfter: false) }

    func programsFor(_ b: Bottle) -> [Program] { programs[b.path] ?? [] }
    func allPrograms() -> [(Bottle, Program)] {
        bottles.flatMap { b in programsFor(b).map { (b, $0) } }
    }

    // convenience setters ----------------------------------------------------
    func setConfig(_ b: Bottle, _ pairs: [String]) {
        cli(["config", "-b", b.path, "set"] + pairs)
    }
    func setBackend(_ b: Bottle, _ backend: String) { setConfig(b, ["backend=\(backend)"]) }
    func setToggle(_ b: Bottle, key: String, on: Bool) {
        setConfig(b, ["\(key)=\(on ? "1" : "0")"])
    }
}
