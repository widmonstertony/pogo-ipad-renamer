import AppKit
import Combine
import Darwin
import Foundation
import SwiftUI

// The desktop shell deliberately performs no device automation itself.  It
// reads the same local artifacts as the former Tk monitor and asks the
// existing guarded Python launcher to start, pause, resume, or stop a batch.

private extension Locale {
    var prefersChinese: Bool {
        identifier.lowercased().hasPrefix("zh")
            || Locale.preferredLanguages.first?.lowercased().hasPrefix("zh") == true
    }
}

private func t(_ zh: String, _ en: String, locale: Locale) -> String {
    locale.prefersChinese ? zh : en
}

private enum InterfaceLanguage: String, CaseIterable, Identifiable {
    case system
    case chinese
    case english

    var id: String { rawValue }

    var locale: Locale {
        switch self {
        case .system: return .autoupdatingCurrent
        case .chinese: return Locale(identifier: "zh-Hans")
        case .english: return Locale(identifier: "en")
        }
    }

    func title(locale: Locale) -> String {
        switch self {
        case .system: return t("跟随系统", "System", locale: locale)
        case .chinese: return "中文"
        case .english: return "English"
        }
    }
}

private enum InterfaceAppearance: String, CaseIterable, Identifiable {
    case system
    case light
    case dark

    var id: String { rawValue }

    var colorScheme: ColorScheme? {
        switch self {
        case .system: return nil
        case .light: return .light
        case .dark: return .dark
        }
    }

    func title(locale: Locale) -> String {
        switch self {
        case .system: return t("跟随系统", "System", locale: locale)
        case .light: return t("浅色", "Light", locale: locale)
        case .dark: return t("深色", "Dark", locale: locale)
        }
    }
}

private enum AppRoot {
    static func resolve() -> URL {
        let environment = ProcessInfo.processInfo.environment
        if let configured = environment["POGO_APP_ROOT"], !configured.isEmpty {
            return URL(fileURLWithPath: configured, isDirectory: true)
        }
        let bundleProject = Bundle.main.bundleURL
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let candidates = [
            bundleProject,
            URL(fileURLWithPath: FileManager.default.currentDirectoryPath, isDirectory: true),
        ]
        for initialCandidate in candidates {
            var candidate = initialCandidate
            for _ in 0..<6 {
                if FileManager.default.fileExists(atPath: candidate.appendingPathComponent("pyproject.toml").path) {
                    return candidate
                }
                candidate.deleteLastPathComponent()
            }
        }
        return URL(fileURLWithPath: FileManager.default.currentDirectoryPath, isDirectory: true)
    }
}

private enum LocalFileIO {
    /// Read a small local dashboard artifact without Foundation's extended
    /// attribute lookup.  On this macOS 27 installation that lookup can hang
    /// for a Documents-hosted project even though ordinary POSIX reads are
    /// healthy.  The dashboard only consumes its own JSON/JPEG/log files.
    static func read(_ url: URL) -> Data? {
        let descriptor = Darwin.open(url.path, O_RDONLY)
        guard descriptor >= 0 else { return nil }
        defer { Darwin.close(descriptor) }

        var result = Data()
        var buffer = [UInt8](repeating: 0, count: 32 * 1024)
        while true {
            let count = buffer.withUnsafeMutableBytes { bytes in
                Darwin.read(descriptor, bytes.baseAddress, bytes.count)
            }
            if count < 0 { return nil }
            if count == 0 { return result }
            result.append(buffer, count: count)
        }
    }
}

private struct AppSettings: Codable {
    var mcp_url: String = "http://127.0.0.1:8090/mcp"
    var ollama_url: String = "http://127.0.0.1:11434"
    var model: String = "RapidOCR + 像素测量（无需设置）"
    var batch_limit: Int = 100
    var unlimited: Bool = true

    var healthURL: URL? {
        let trimmed = mcp_url.trimmingCharacters(in: .whitespacesAndNewlines)
        let base = trimmed.hasSuffix("/mcp") ? String(trimmed.dropLast(4)) : trimmed
        return URL(string: base + "/health")
    }
}

private struct BatchProgress: Codable {
    var current: Int?
    var limit: Int?
    var phase: String?
    var renamed: Int?
    var skipped: Int?
    var scanned: Int?
    var unreadable: Int?
    var verification: VerificationInfo?
}

private struct VerificationInfo: Codable {
    var target: Int?
    var completed: Int?
    var passed: Bool?
}

private struct BatchState: Codable {
    var status: String?
    var progress: BatchProgress?
    var reconnects: Int?
    var started_at: String?
}

private struct PokemonIdentity: Codable {
    var name: String?
    var species: String?
    var is_default: Bool?
}

private struct IVInfo: Codable {
    var attack: Int?
    var defense: Int?
    var stamina: Int?
    var percent: Int?
    var confidence: Double?
}

private struct LiveActivity: Codable {
    var progress: BatchProgress?
    var pokemon: PokemonIdentity?
    var screen: String?
    var step: String?
    var waiting: WaitingInfo?
    var item_result: String?
    var iv: IVInfo?
    var nickname: String?
    var last_result: String?
    var attention: AttentionInfo?
    var updated_at: String?
}

private struct WaitingInfo: Codable {
    var stage: String?
    var reason: String?
    var attempt: Int?
    var total: Int?
    var elapsed_seconds: Int?
    var next_action: String?
    var user_action: String?
}

private struct AttentionInfo: Codable {
    var required: Bool?
    var reason: String?
    var user_action: String?
}

private enum RunCommand: Identifiable, Equatable {
    case rename
    case scan
    case pause
    case resume
    case stop

    var id: String {
        switch self {
        case .rename: return "rename"
        case .scan: return "scan"
        case .pause: return "pause"
        case .resume: return "resume"
        case .stop: return "stop"
        }
    }

    var arguments: [String] {
        switch self {
        case .rename:
            return ["-m", "pogo_iphone_renamer.headless_batch_launcher", "--root", "__ROOT__", "--mode", "rename"]
        case .scan:
            return ["-m", "pogo_iphone_renamer.headless_batch_launcher", "--root", "__ROOT__", "--mode", "scan"]
        case .pause:
            return ["-m", "pogo_iphone_renamer.headless_batch_launcher", "--root", "__ROOT__", "--pause"]
        case .resume:
            return ["-m", "pogo_iphone_renamer.headless_batch_launcher", "--root", "__ROOT__", "--resume"]
        case .stop:
            return ["-m", "pogo_iphone_renamer.headless_batch_launcher", "--root", "__ROOT__", "--stop"]
        }
    }
}

@MainActor
private final class DashboardStore: ObservableObject {
    @Published var settings: AppSettings
    @Published var state = BatchState()
    @Published var activity = LiveActivity()
    @Published var preview: NSImage?
    @Published var mcpReachable = false
    @Published var connectionDetail = ""
    @Published var logLines: [String] = []
    @Published var actionError: String?

    let root: URL
    private var lastLogOffset = 0
    private var refreshInFlight = false

    init(root: URL) {
        self.root = root
        // Do not synchronously read Documents while SwiftUI is constructing
        // its first window.  On macOS 27 a transient Files/TCC stall can make
        // ``Data(contentsOf:)`` wait long enough for the app to look like it
        // never launched.  The dashboard intentionally starts with safe
        // defaults and swaps in local state after the window is available.
        self.settings = AppSettings()
        self.connectionDetail = "正在读取本机状态镜像…"
        let launchRoot = root
        DispatchQueue.global(qos: .utility).async {
            let loadedSettings = Self.loadSettings(root: launchRoot)
            DispatchQueue.main.async { [weak self] in
                guard let self else { return }
                self.settings = loadedSettings
                self.refresh()
                self.checkConnection()
            }
        }
    }

    var isRunning: Bool {
        ["starting", "running", "waiting_for_mcp"].contains(state.status ?? "")
    }
    var isPauseRequested: Bool {
        FileManager.default.fileExists(atPath: dataDirectory.appendingPathComponent("batch.pause").path)
    }
    var dataDirectory: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("PokemonGOOrganizer", isDirectory: true)
    }

    func refresh() {
        guard !refreshInFlight else { return }
        refreshInFlight = true
        let directory = dataDirectory
        let logOffset = lastLogOffset
        DispatchQueue.global(qos: .utility).async {
            let state = Self.decode(BatchState.self, from: directory.appendingPathComponent("batch-state.json")) ?? BatchState()
            let activity = Self.decode(LiveActivity.self, from: directory.appendingPathComponent("live-activity.json")) ?? LiveActivity()
            let preview = LocalFileIO.read(directory.appendingPathComponent("live-preview.jpg")).flatMap(NSImage.init(data:))
            let logUpdate = Self.readLogLines(from: directory, offset: logOffset)
            DispatchQueue.main.async { [weak self] in
                guard let self else { return }
                self.state = state
                self.activity = activity
                self.preview = preview
                self.lastLogOffset = logUpdate.offset
                self.logLines.append(contentsOf: logUpdate.lines)
                if self.logLines.count > 220 { self.logLines.removeFirst(self.logLines.count - 220) }
                self.refreshInFlight = false
            }
        }
    }

    func saveSettings() {
        do {
            let target = dataDirectory.appendingPathComponent("gui-settings.json")
            try FileManager.default.createDirectory(at: dataDirectory, withIntermediateDirectories: true)
            let encoded = try JSONEncoder.pretty.encode(settings)
            let temporary = target.appendingPathExtension("tmp")
            try encoded.write(to: temporary, options: .atomic)
            if FileManager.default.fileExists(atPath: target.path) {
                _ = try FileManager.default.replaceItemAt(target, withItemAt: temporary)
            } else {
                try FileManager.default.moveItem(at: temporary, to: target)
            }
        } catch {
            actionError = error.localizedDescription
        }
    }

    func checkConnection() {
        guard let url = settings.healthURL else {
            mcpReachable = false
            connectionDetail = "Invalid MCP address"
            return
        }
        URLSession.shared.dataTask(with: url) { [weak self] data, _, error in
            let detail: String
            let reachable: Bool
            if let error {
                detail = error.localizedDescription
                reachable = false
            } else if let data,
                      let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                      String(describing: payload["status"] ?? "") == "ok" {
                detail = String(describing: payload["server"] ?? "iOS MCP")
                reachable = true
            } else {
                detail = "Unexpected health response"
                reachable = false
            }
            DispatchQueue.main.async {
                self?.mcpReachable = reachable
                self?.connectionDetail = detail
            }
        }.resume()
    }

    func run(_ command: RunCommand) {
        if command == .rename || command == .scan { saveSettings() }
        let python = root.appendingPathComponent(".venv/bin/python")
        guard FileManager.default.isExecutableFile(atPath: python.path) else {
            actionError = "Python environment is not ready. Start the app from the macOS launcher once."
            return
        }
        let process = Process()
        process.executableURL = python
        process.currentDirectoryURL = root
        process.arguments = command.arguments.map { $0 == "__ROOT__" ? root.path : $0 }
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONPATH"] = root.appendingPathComponent("src").path
        environment["POGO_APP_ROOT"] = root.path
        process.environment = environment
        do {
            try process.run()
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { self.refresh() }
        } catch {
            actionError = error.localizedDescription
        }
    }

    nonisolated private static func readLogLines(
        from directory: URL,
        offset: Int
    ) -> (lines: [String], offset: Int) {
        let logURL = directory.appendingPathComponent("background-worker.log")
        guard let contents = LocalFileIO.read(logURL) else { return ([], offset) }
        let currentSize = contents.count
        let adjustedOffset = currentSize < offset ? 0 : offset
        guard currentSize > adjustedOffset,
              let text = String(data: contents.dropFirst(adjustedOffset), encoding: .utf8) else {
            return ([], adjustedOffset)
        }
        let additions = text.split(whereSeparator: \.isNewline).map(String.init)
        return (additions, currentSize)
    }

    nonisolated private static func loadSettings(root: URL) -> AppSettings {
        decode(AppSettings.self, from: root.appendingPathComponent(".pogo-data/gui-settings.json")) ?? AppSettings()
    }

    nonisolated private static func decode<T: Decodable>(_ type: T.Type, from url: URL) -> T? {
        guard let data = LocalFileIO.read(url) else { return nil }
        return try? JSONDecoder().decode(type, from: data)
    }
}

private extension JSONEncoder {
    static var pretty: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        return encoder
    }
}

private enum SidebarSection: Hashable {
    case workspace
    case activity
    case preferences
}

@main
struct PokemonGoOrganizerApp: App {
    @StateObject private var store: DashboardStore
    @AppStorage("interfaceLanguage") private var interfaceLanguage = InterfaceLanguage.system.rawValue
    @AppStorage("interfaceAppearance") private var interfaceAppearance = InterfaceAppearance.system.rawValue

    init() {
        _store = StateObject(wrappedValue: DashboardStore(root: AppRoot.resolve()))
    }

    var body: some Scene {
        WindowGroup {
            OrganizerWindow(store: store)
                .frame(minWidth: 900, minHeight: 640)
                .environment(\.locale, selectedLanguage.locale)
                .preferredColorScheme(selectedAppearance.colorScheme)
        }
        .windowStyle(.titleBar)
        .windowToolbarStyle(.unifiedCompact)
    }

    private var selectedLanguage: InterfaceLanguage {
        InterfaceLanguage(rawValue: interfaceLanguage) ?? .system
    }

    private var selectedAppearance: InterfaceAppearance {
        InterfaceAppearance(rawValue: interfaceAppearance) ?? .system
    }
}

private struct OrganizerWindow: View {
    @ObservedObject var store: DashboardStore
    @Environment(\.locale) private var locale
    @State private var selection: SidebarSection? = .workspace
    @State private var pendingAction: RunCommand?

    var body: some View {
        NavigationSplitView {
            List(selection: $selection) {
                Section(t("工作区", "WORKSPACE", locale: locale)) {
                    Label(t("任务总览", "Task overview", locale: locale), systemImage: "rectangle.3.group.fill")
                        .tag(SidebarSection.workspace)
                    Label(t("活动记录", "Activity", locale: locale), systemImage: "clock.arrow.circlepath")
                        .tag(SidebarSection.activity)
                }

                Section(t("设置", "SETTINGS", locale: locale)) {
                    Label(t("偏好设置", "Preferences", locale: locale), systemImage: "gearshape")
                        .tag(SidebarSection.preferences)
                }
            }
            .listStyle(.sidebar)
            .navigationTitle("Pokémon GO")
            .navigationSplitViewColumnWidth(min: 190, ideal: 224, max: 280)
            .safeAreaInset(edge: .bottom, spacing: 0) {
                ConnectionFooter(store: store, locale: locale)
                    .padding(12)
            }
        } detail: {
            Group {
                switch selection ?? .workspace {
                case .workspace:
                    OverviewView(store: store, locale: locale, requestAction: requestAction)
                case .activity:
                    ActivityView(store: store, locale: locale)
                case .preferences:
                    SettingsView(store: store, locale: locale)
                }
            }
            .toolbar {
                ToolbarItemGroup(placement: .primaryAction) {
                    Button(action: store.refresh) {
                        Image(systemName: "arrow.clockwise")
                    }
                    .help(t("刷新本地状态", "Refresh local status", locale: locale))
                    Button(action: store.checkConnection) {
                        Image(systemName: "network")
                    }
                    .help(t("检查 iPad MCP 连接", "Check iPad MCP connection", locale: locale))
                }
            }
        }
        .navigationSplitViewStyle(.balanced)
        .onReceive(Timer.publish(every: 0.8, on: .main, in: .common).autoconnect()) { _ in
            store.refresh()
        }
        .alert(item: $pendingAction) { action in
            confirmation(for: action)
        }
        .alert(
            t("无法执行操作", "Unable to perform action", locale: locale),
            isPresented: Binding(
                get: { store.actionError != nil },
                set: { if !$0 { store.actionError = nil } }
            )
        ) {
            Button(t("好", "OK", locale: locale), role: .cancel) { store.actionError = nil }
        } message: {
            Text(store.actionError ?? "")
        }
    }

    private func requestAction(_ action: RunCommand) {
        if action == .rename || action == .stop { pendingAction = action } else { store.run(action) }
    }

    private func confirmation(for action: RunCommand) -> Alert {
        if action == .stop {
            return Alert(
                title: Text(t("安全停止任务？", "Safely stop the task?", locale: locale)),
                message: Text(t(
                    "程序会先离开输入与弹窗状态，再释放后台运行器；不会传送、删除或重启游戏。",
                    "The worker exits input and dialog states before stopping; it won’t transfer, delete, or relaunch the game.",
                    locale: locale
                )),
                primaryButton: .destructive(Text(t("停止", "Stop", locale: locale))) { store.run(action) },
                secondaryButton: .cancel(Text(t("取消", "Cancel", locale: locale)))
            )
        }
        return Alert(
            title: Text(t("开始批量改名？", "Start batch renaming?", locale: locale)),
            message: Text(t(
                "会从 iPad 当前已确认的游戏画面安全接续（详情、盒子、菜单或地图）。已有昵称不会被改动；不会重开 Pokémon GO。",
                "The batch safely resumes from the confirmed game screen on iPad (detail, storage, menu, or map). Existing nicknames stay unchanged and Pokémon GO won’t be relaunched.",
                locale: locale
            )),
            primaryButton: .default(Text(t("开始", "Start", locale: locale))) { store.run(action) },
            secondaryButton: .cancel(Text(t("取消", "Cancel", locale: locale)))
        )
    }
}

private struct OverviewView: View {
    @ObservedObject var store: DashboardStore
    let locale: Locale
    let requestAction: (RunCommand) -> Void

    private var progress: BatchProgress { store.activity.progress ?? store.state.progress ?? BatchProgress() }
    private var displayedProgress: BatchProgress {
        var value = progress
        // Never trust legacy acceptance records that counted skipped cards.
        // Derive the visible check from verified renames in every run state.
        if value.renamed != nil {
            let completed = value.renamed ?? 0
            let target = value.verification?.target ?? store.state.progress?.verification?.target ?? 50
            value.verification = VerificationInfo(
                target: target,
                completed: completed,
                passed: completed >= target
            )
        }
        return value
    }
    private var pokemonName: String {
        store.activity.pokemon?.name ?? t("等待详情身份确认", "Waiting for Pokémon identity", locale: locale)
    }

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 24) {
                HStack(alignment: .firstTextBaseline, spacing: 16) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(t("批量整理", "Batch organizer", locale: locale))
                            .font(.largeTitle.weight(.bold))
                        Text(t("从 iPad 当前已确认的游戏画面安全接续。", "Safely resume from the confirmed game screen on iPad.", locale: locale))
                            .foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 16)
                    StatusBadge(status: store.state.status, locale: locale)
                }

                LiveSessionCard(
                    image: store.preview,
                    pokemonName: pokemonName,
                    screen: screenTitle(store.activity.screen),
                    step: store.activity.step ?? t("等待工作进程", "Waiting for the worker", locale: locale),
                    waiting: store.activity.waiting,
                    attention: store.activity.attention,
                    itemResult: store.activity.item_result,
                    terminalResult: store.activity.last_result,
                    batchStatus: store.state.status,
                    phase: displayedProgress.phase,
                    verification: displayedProgress.verification,
                    iv: store.activity.iv.map(ivLine),
                    nickname: store.activity.nickname,
                    updated: lastUpdated,
                    secondsSinceUpdate: secondsSinceUpdate,
                    locale: locale
                )

                VStack(alignment: .leading, spacing: 12) {
                    Text(t("本次会话", "This session", locale: locale))
                        .font(.headline)
                    MetricsView(progress: displayedProgress, locale: locale)
                }

                ActionDock(
                    isRunning: store.isRunning,
                    isPauseRequested: store.isPauseRequested,
                    locale: locale,
                    requestAction: requestAction
                )

                DisclosureGroup(t("处理安全边界", "Processing safeguards", locale: locale)) {
                    VStack(alignment: .leading, spacing: 10) {
                        SafetyRule(
                            t("只处理默认繁中名称；已有自定义或 IV 昵称的宝可梦会跳过。", "Only default Traditional Chinese names are processed; custom or IV nicknames are skipped.", locale: locale),
                            icon: "checkmark.shield"
                        )
                        SafetyRule(
                            t("无法可靠读出 IV 时保留原名并继续，不会猜测。", "When IVs can’t be read reliably, the original name stays unchanged; the app never guesses.", locale: locale),
                            icon: "eye.slash"
                        )
                        SafetyRule(
                            t("锁屏、断线或未知页面会安全暂停；程序不会重新打开 Pokémon GO。", "A lock, disconnect, or unknown screen pauses safely; Pokémon GO is never reopened.", locale: locale),
                            icon: "lock"
                        )
                    }
                    .padding(.top, 8)
                }
                .font(.subheadline)
                .foregroundStyle(.secondary)
            }
            .frame(maxWidth: 1_100, alignment: .leading)
            .padding(.horizontal, 32)
            .padding(.vertical, 28)
        }
        .navigationTitle(t("任务总览", "Task overview", locale: locale))
    }

    private var lastUpdated: String {
        let value = store.activity.updated_at?.replacingOccurrences(of: "T", with: " ").replacingOccurrences(of: "+00:00", with: " UTC")
        return value.map { t("状态更新：\($0)", "Updated: \($0)", locale: locale) }
            ?? t("等待首张 iPad 画面", "Waiting for the first iPad frame", locale: locale)
    }

    private var secondsSinceUpdate: Int? {
        guard let value = store.activity.updated_at,
              let date = ISO8601DateFormatter().date(from: value) else { return nil }
        return max(0, Int(Date().timeIntervalSince(date)))
    }

    private func ivLine(_ iv: IVInfo) -> String {
        let numbers = "A/D/S=\(iv.attack ?? 0)/\(iv.defense ?? 0)/\(iv.stamina ?? 0)"
        return iv.percent.map { "\(numbers) · IV=\($0)%" } ?? numbers
    }

    private func screenTitle(_ raw: String?) -> String {
        let labels = [
            "DETAIL": t("宝可梦详情页", "Pokémon detail", locale: locale),
            "DETAIL_MENU": t("详情菜单", "Detail menu", locale: locale),
            "APPRAISAL": t("鉴定页", "Appraisal", locale: locale),
            "APPRAISAL_BARS": t("鉴定条页面", "Appraisal bars", locale: locale),
            "RENAME_DIALOG": t("改名输入框", "Rename field", locale: locale),
        ]
        return labels[raw ?? ""] ?? (raw ?? t("等待识别", "Waiting for recognition", locale: locale))
    }
}

private struct ConnectionFooter: View {
    @ObservedObject var store: DashboardStore
    let locale: Locale

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: store.mcpReachable ? "checkmark.circle.fill" : "circle.dashed")
                .foregroundStyle(store.mcpReachable ? .green : .secondary)
            VStack(alignment: .leading, spacing: 1) {
                Text(store.mcpReachable ? t("iPad 已连接", "iPad connected", locale: locale) : t("等待 iPad 连接", "Waiting for iPad", locale: locale))
                    .font(.caption.weight(.medium))
                Text(store.connectionDetail.isEmpty ? t("本机 MCP", "Local MCP", locale: locale) : store.connectionDetail)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 0)
        }
        .padding(10)
        .background(.quaternary, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
    }
}

private struct LiveSessionCard: View {
    let image: NSImage?
    let pokemonName: String
    let screen: String
    let step: String
    let waiting: WaitingInfo?
    let attention: AttentionInfo?
    let itemResult: String?
    let terminalResult: String?
    let batchStatus: String?
    let phase: String?
    let verification: VerificationInfo?
    let iv: String?
    let nickname: String?
    let updated: String
    let secondsSinceUpdate: Int?
    let locale: Locale

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Label(t("实时 iPad", "Live iPad", locale: locale), systemImage: "ipad.landscape")
                    .font(.headline)
                Spacer()
                VStack(alignment: .trailing, spacing: 2) {
                    Text(updated)
                    if let secondsSinceUpdate, secondsSinceUpdate >= 15 {
                        Text(t(
                            "已 \(secondsSinceUpdate) 秒没有新的可验证事件",
                            "No new verified event for \(secondsSinceUpdate)s",
                            locale: locale
                        ))
                        .foregroundStyle(.orange)
                    }
                }
                .font(.caption)
                .foregroundStyle(.tertiary)
            }

            HStack(alignment: .top, spacing: 24) {
                PreviewView(image: image, locale: locale)
                    .frame(width: 252, height: 318)
                Divider()
                    .frame(height: 318)
                VStack(alignment: .leading, spacing: 14) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(pokemonName)
                            .font(.title2.weight(.semibold))
                            .textSelection(.enabled)
                        Label(screen, systemImage: "rectangle.on.rectangle")
                            .foregroundStyle(.secondary)
                    }

                    Divider()

                    Text(step)
                        .font(.body)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)

                    TransparencyPanel(
                        waiting: waiting,
                        attention: attention,
                        itemResult: itemResult,
                        terminalResult: terminalResult,
                        batchStatus: batchStatus,
                        phase: phase,
                        verification: verification,
                        secondsSinceUpdate: secondsSinceUpdate,
                        locale: locale
                    )

                    if let iv {
                        DetailValue(title: t("鉴定", "Appraisal", locale: locale), value: iv, icon: "chart.bar.fill")
                    }
                    if let nickname, !nickname.isEmpty {
                        DetailValue(title: t("新昵称", "New nickname", locale: locale), value: nickname, icon: "pencil")
                    }
                    Spacer(minLength: 0)
                }
                .frame(maxWidth: .infinity, minHeight: 318, alignment: .leading)
            }
        }
        .padding(20)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .strokeBorder(.quaternary, lineWidth: 1)
        }
    }
}

private struct TransparencyPanel: View {
    let waiting: WaitingInfo?
    let attention: AttentionInfo?
    let itemResult: String?
    let terminalResult: String?
    let batchStatus: String?
    let phase: String?
    let verification: VerificationInfo?
    let secondsSinceUpdate: Int?
    let locale: Locale

    private var isActive: Bool {
        ["starting", "running", "waiting_for_mcp"].contains(batchStatus ?? "")
    }

    private var phaseText: String {
        if batchStatus == "finished" { return t("任务已结束", "Task ended", locale: locale) }
        if batchStatus == "failed" { return t("任务已安全停止", "Task stopped safely", locale: locale) }
        switch phase {
        case "processing": return t("正在处理本只", "Processing this Pokémon", locale: locale)
        case "completed": return t("本只已完成，正在核验下一只", "This Pokémon is complete; verifying the next", locale: locale)
        case "paused": return t("已在安全边界暂停", "Paused at a safe boundary", locale: locale)
        case "resumed": return t("已恢复，正在复核本只", "Resumed; rechecking this Pokémon", locale: locale)
        default: return t("正在等待下一步", "Waiting for the next step", locale: locale)
        }
    }

    private var attemptText: String? {
        guard let waiting, let attempt = waiting.attempt else { return nil }
        if let total = waiting.total { return "\(attempt) / \(total)" }
        return "#\(attempt)"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: panelIcon)
                    .foregroundStyle(panelColor)
                Text(panelTitle)
                    .font(.subheadline.weight(.semibold))
                Spacer(minLength: 8)
                Text(phaseText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            if let attention, attention.required == true {
                ExplanationRow(
                    title: t("原因", "Reason", locale: locale),
                    value: attention.reason ?? t("任务已安全停止。", "The task stopped safely.", locale: locale),
                    icon: "exclamationmark.triangle.fill"
                )
                ExplanationRow(
                    title: t("你需要", "You need to", locale: locale),
                    value: attention.user_action ?? t("请查看活动记录。", "Check Activity.", locale: locale),
                    icon: "hand.raised.fill"
                )
            } else if let waiting {
                if let stage = waiting.stage, !stage.isEmpty {
                    ExplanationRow(
                        title: t("正在等", "Waiting for", locale: locale),
                        value: stage,
                        icon: "hourglass"
                    )
                }
                if let reason = waiting.reason, !reason.isEmpty {
                    ExplanationRow(
                        title: t("原因", "Reason", locale: locale),
                        value: reason,
                        icon: "text.magnifyingglass"
                    )
                }
                HStack(spacing: 14) {
                    if let attemptText {
                        DetailPill(title: t("尝试", "Attempt", locale: locale), value: attemptText)
                    }
                    if let elapsed = waiting.elapsed_seconds {
                        DetailPill(title: t("已等待", "Elapsed", locale: locale), value: duration(elapsed))
                    }
                }
                if let next = waiting.next_action, !next.isEmpty {
                    ExplanationRow(
                        title: t("下一步", "Next", locale: locale),
                        value: next,
                        icon: "arrow.right.circle"
                    )
                }
                ExplanationRow(
                    title: t("你需要", "You need to", locale: locale),
                    value: waiting.user_action ?? t("无需操作。", "No action needed.", locale: locale),
                    icon: "person.fill.checkmark"
                )
            } else if isActive, let secondsSinceUpdate, secondsSinceUpdate >= 15 {
                ExplanationRow(
                    title: t("原因", "Reason", locale: locale),
                    value: t(
                        "后台尚未写入新的可验证事件；iPad 截图或 OCR 可能正在重试。",
                        "The worker has not written a new verifiable event; iPad capture or OCR may be retrying.",
                        locale: locale
                    ),
                    icon: "eye.trianglebadge.exclamationmark"
                )
                ExplanationRow(
                    title: t("下一步", "Next", locale: locale),
                    value: t(
                        "后台只读复核，不会盲点或额外滑动；若必须人工介入，这里会直接说明。",
                        "The worker is rechecking read-only; it will not tap blindly or add a swipe. If intervention is required, it will be stated here.",
                        locale: locale
                    ),
                    icon: "arrow.triangle.2.circlepath"
                )
            } else if !isActive {
                ExplanationRow(
                    title: t("当前状态", "Current state", locale: locale),
                    value: t(
                        "后台没有在执行；请以“任务结果”和“连续验收”为准。",
                        "No worker is running. See Task result and Run check.",
                        locale: locale
                    ),
                    icon: "stop.circle"
                )
            } else {
                ExplanationRow(
                    title: t("下一步", "Next", locale: locale),
                    value: t(
                        "后台正在执行上方步骤；当前不需要你操作。",
                        "The worker is performing the step above; no action is needed from you.",
                        locale: locale
                    ),
                    icon: "checkmark.circle"
                )
            }

            if let itemResult, !itemResult.isEmpty {
                ExplanationRow(
                    title: t("本只结果", "This Pokémon", locale: locale),
                    value: itemResult,
                    icon: "checkmark.seal"
                )
            } else if !isActive, let terminalResult, !terminalResult.isEmpty {
                ExplanationRow(
                    title: t("任务结果", "Task result", locale: locale),
                    value: terminalResult,
                    icon: "flag.checkered"
                )
            }

            if let verification, let target = verification.target, let completed = verification.completed {
                let passed = verification.passed == true
                ExplanationRow(
                    title: t("真实改名验收", "Verified rename check", locale: locale),
                    value: passed
                        ? t("已通过：\(completed) / \(target) 只", "Passed: \(completed) / \(target) Pokémon", locale: locale)
                        : t("未通过：\(completed) / \(target) 只；不能视为完成。", "Not passed: \(completed) / \(target) Pokémon; this is not complete.", locale: locale),
                    icon: passed ? "checkmark.seal.fill" : "exclamationmark.triangle"
                )
            }
        }
        .padding(12)
        .background(panelColor.opacity(0.08), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .strokeBorder(panelColor.opacity(0.18), lineWidth: 1)
        }
    }

    private var panelTitle: String {
        if batchStatus == "finished" { return t("任务已结束", "Task ended", locale: locale) }
        if attention?.required == true { return t("需要你操作", "Action needed", locale: locale) }
        if waiting != nil { return t("后台正在安全等待", "Safely waiting in the background", locale: locale) }
        return t("自动化状态", "Automation status", locale: locale)
    }

    private var panelIcon: String {
        if batchStatus == "finished" { return "flag.checkered" }
        if attention?.required == true { return "exclamationmark.triangle.fill" }
        if waiting != nil { return "hourglass" }
        return "checkmark.shield.fill"
    }

    private var panelColor: Color {
        if batchStatus == "finished" { return .green }
        if attention?.required == true { return .orange }
        if waiting != nil { return .blue }
        return .secondary
    }

    private func duration(_ seconds: Int) -> String {
        if seconds < 60 { return t("\(seconds) 秒", "\(seconds)s", locale: locale) }
        return t("\(seconds / 60) 分 \(seconds % 60) 秒", "\(seconds / 60)m \(seconds % 60)s", locale: locale)
    }
}

private struct ExplanationRow: View {
    let title: String
    let value: String
    let icon: String

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: icon)
                .frame(width: 16)
                .foregroundStyle(.secondary)
            Text(title)
                .foregroundStyle(.secondary)
                .frame(width: 48, alignment: .leading)
            Text(value)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
            Spacer(minLength: 0)
        }
        .font(.caption)
    }
}

private struct DetailPill: View {
    let title: String
    let value: String

    var body: some View {
        HStack(spacing: 5) {
            Text(title).foregroundStyle(.secondary)
            Text(value).monospacedDigit().fontWeight(.medium)
        }
        .font(.caption)
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(.quaternary, in: Capsule())
    }
}

private struct PreviewView: View {
    let image: NSImage?
    let locale: Locale

    var body: some View {
        Group {
            if let image {
                Image(nsImage: image)
                    .resizable()
                    .interpolation(.high)
                    .antialiased(true)
                    .scaledToFit()
                    .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            } else {
                ContentUnavailableView(
                    t("等待画面", "Waiting for preview", locale: locale),
                    systemImage: "ipad.landscape",
                    description: Text(t("首次状态画面会显示在这里。", "The first status frame appears here.", locale: locale))
                )
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(.quaternary, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }
}

private struct DetailValue: View {
    let title: String
    let value: String
    let icon: String

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Image(systemName: icon)
                .foregroundStyle(.secondary)
            Text(title)
                .foregroundStyle(.secondary)
            Spacer(minLength: 12)
            Text(value)
                .multilineTextAlignment(.trailing)
                .textSelection(.enabled)
        }
        .font(.subheadline)
    }
}

private struct ActionDock: View {
    let isRunning: Bool
    let isPauseRequested: Bool
    let locale: Locale
    let requestAction: (RunCommand) -> Void

    var body: some View {
        HStack(spacing: 10) {
            Button {
                requestAction(.rename)
            } label: {
                Label(t("开始批量改名", "Start batch rename", locale: locale), systemImage: "play.fill")
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .disabled(isRunning)

            Button {
                requestAction(.scan)
            } label: {
                Label(t("只读扫描", "Read-only scan", locale: locale), systemImage: "eye")
            }
            .buttonStyle(.bordered)
            .controlSize(.large)
            .disabled(isRunning)

            Divider()
                .frame(height: 24)

            Button {
                requestAction(isPauseRequested ? .resume : .pause)
            } label: {
                Label(
                    isPauseRequested ? t("继续", "Resume", locale: locale) : t("安全暂停", "Safe pause", locale: locale),
                    systemImage: isPauseRequested ? "play.fill" : "pause.fill"
                )
            }
            .buttonStyle(.bordered)
            .controlSize(.large)
            .disabled(!isRunning)

            Spacer(minLength: 12)

            Button(role: .destructive) {
                requestAction(.stop)
            } label: {
                Label(t("停止", "Stop", locale: locale), systemImage: "stop.fill")
            }
            .buttonStyle(.bordered)
            .controlSize(.large)
            .disabled(!isRunning)
        }
        .padding(12)
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
    }
}

private struct SafetyRule: View {
    let text: String
    let icon: String

    init(_ text: String, icon: String) {
        self.text = text
        self.icon = icon
    }

    var body: some View {
        Label(text, systemImage: icon)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct MetricsView: View {
    let progress: BatchProgress
    let locale: Locale

    var body: some View {
        Grid(horizontalSpacing: 10, verticalSpacing: 10) {
            GridRow {
                Metric(title: t("当前位置", "Position", locale: locale), value: position, icon: "location")
                Metric(title: t("已改名", "Renamed", locale: locale), value: "\(progress.renamed ?? 0)", icon: "pencil.line")
                Metric(title: t("已有昵称跳过", "Skipped", locale: locale), value: "\(progress.skipped ?? 0)", icon: "forward.end")
                Metric(title: t("安全保留", "Kept safe", locale: locale), value: "\(progress.unreadable ?? 0)", icon: "shield")
            }
            if let verification = progress.verification,
               let target = verification.target,
               let completed = verification.completed {
                GridRow {
                    Metric(
                        title: t("真实改名验收", "Verified rename check", locale: locale),
                        value: "\(completed) / \(target)",
                        icon: verification.passed == true ? "checkmark.seal" : "exclamationmark.triangle"
                    )
                    Color.clear.gridCellColumns(3)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var position: String {
        guard let current = progress.current else { return "—" }
        if let limit = progress.limit { return "\(current) / \(limit)" }
        return "#\(current)"
    }
}

private struct Metric: View {
    let title: String
    let value: String
    let icon: String
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: icon)
                .font(.headline)
                .foregroundStyle(.secondary)
                .frame(width: 18)
            VStack(alignment: .leading, spacing: 2) {
                Text(value).font(.title3.monospacedDigit().weight(.semibold))
                Text(title).font(.caption).foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, minHeight: 68, alignment: .leading)
        .padding(12)
        .background(.quaternary, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }
}

private struct StatusBadge: View {
    let status: String?
    let locale: Locale
    var body: some View {
        Label(text, systemImage: icon)
            .font(.subheadline.weight(.medium))
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(.quaternary, in: Capsule())
            .foregroundStyle(color)
    }
    private var text: String {
        switch status {
        case "running": return t("正在运行", "Running", locale: locale)
        case "starting": return t("正在启动", "Starting", locale: locale)
        case "waiting_for_mcp": return t("等待 MCP", "Waiting for MCP", locale: locale)
        case "finished": return t("已结束", "Finished", locale: locale)
        case "failed": return t("需要注意", "Needs attention", locale: locale)
        case "stopped": return t("已停止", "Stopped", locale: locale)
        default: return t("待机", "Idle", locale: locale)
        }
    }
    private var icon: String {
        switch status {
        case "running": return "circle.fill"
        case "finished": return "flag.checkered"
        case "failed": return "exclamationmark.triangle.fill"
        default: return "circle"
        }
    }
    private var color: Color {
        switch status {
        case "running": return .green
        case "finished": return .secondary
        case "failed", "waiting_for_mcp": return .orange
        default: return .secondary
        }
    }
}

private struct ActivityView: View {
    @ObservedObject var store: DashboardStore
    let locale: Locale

    var body: some View {
        Group {
            if store.logLines.isEmpty {
                ContentUnavailableView(
                    t("暂无活动记录", "No activity yet", locale: locale),
                    systemImage: "text.alignleft",
                    description: Text(t("后台任务的最新记录会显示在这里。", "Latest worker records will appear here." , locale: locale))
                )
            } else {
                List(store.logLines, id: \.self) { line in
                    Text(line)
                        .font(.system(.body, design: .monospaced))
                        .textSelection(.enabled)
                        .padding(.vertical, 4)
                }
                .listStyle(.inset)
            }
        }
        .navigationTitle(t("活动记录", "Activity", locale: locale))
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button {
                    store.logLines.removeAll()
                } label: {
                    Label(t("清空显示", "Clear", locale: locale), systemImage: "trash")
                }
                .help(t("只清空本窗口的日志显示", "Clear only the log shown in this window", locale: locale))
                    .disabled(store.logLines.isEmpty)
            }
        }
    }
}

private struct SettingsView: View {
    @ObservedObject var store: DashboardStore
    let locale: Locale
    @AppStorage("interfaceLanguage") private var interfaceLanguage = InterfaceLanguage.system.rawValue
    @AppStorage("interfaceAppearance") private var interfaceAppearance = InterfaceAppearance.system.rawValue

    var body: some View {
        Form {
            Section(t("外观与语言", "Appearance & language", locale: locale)) {
                Picker(t("界面语言", "Interface language", locale: locale), selection: $interfaceLanguage) {
                    ForEach(InterfaceLanguage.allCases) { option in
                        Text(option.title(locale: locale)).tag(option.rawValue)
                    }
                }
                .pickerStyle(.menu)

                Picker(t("外观", "Appearance", locale: locale), selection: $interfaceAppearance) {
                    ForEach(InterfaceAppearance.allCases) { option in
                        Text(option.title(locale: locale)).tag(option.rawValue)
                    }
                }
                .pickerStyle(.menu)

                Text(t(
                    "语言和浅深色会立即应用，并在下次启动时保留。",
                    "Language and appearance apply immediately and are remembered for the next launch.",
                    locale: locale
                ))
                .font(.footnote)
                .foregroundStyle(.secondary)
            }

            Section(t("iPad 连接", "iPad connection", locale: locale)) {
                TextField(t("MCP 地址", "MCP URL", locale: locale), text: $store.settings.mcp_url)
                    .textFieldStyle(.roundedBorder)
                LabeledContent(t("状态", "Status", locale: locale)) {
                    Label(
                        store.mcpReachable ? t("已连接", "Connected", locale: locale) : t("未连接", "Not connected", locale: locale),
                        systemImage: store.mcpReachable ? "checkmark.circle.fill" : "exclamationmark.triangle.fill"
                    )
                    .foregroundStyle(store.mcpReachable ? .green : .secondary)
                }
                HStack {
                    Text(store.connectionDetail).foregroundStyle(.secondary).lineLimit(1)
                    Spacer()
                    Button(t("检查连接", "Check connection", locale: locale)) { store.checkConnection() }
                }
            }

            Section(t("批量范围", "Batch scope", locale: locale)) {
                Toggle(t("不限量（直到盒子末尾或手动停止）", "Unlimited (until box end or stopped manually)", locale: locale), isOn: $store.settings.unlimited)
                Stepper(value: $store.settings.batch_limit, in: 1...100000) {
                    HStack {
                        Text(t("停止数量", "Stop after", locale: locale))
                        Spacer()
                        Text("\(store.settings.batch_limit)").monospacedDigit().foregroundStyle(.secondary)
                    }
                }
                .disabled(store.settings.unlimited)
            }

            Section {
                Button(t("保存连接与范围", "Save connection & scope", locale: locale)) { store.saveSettings() }
                Text(t("连接与批量范围会保存到本机；不会更改 iPad 或游戏设置。", "Connection and batch scope are saved locally; iPad and game settings are never changed.", locale: locale))
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .padding(.horizontal, 32)
        .padding(.vertical, 24)
        .navigationTitle(t("偏好设置", "Preferences", locale: locale))
    }
}
