import AppKit
import WebKit

final class WindowDragView: NSView {
    override var mouseDownCanMoveWindow: Bool { true }

    override func mouseDown(with event: NSEvent) {
        window?.performDrag(with: event)
    }
}

final class PasteAwareWebView: WKWebView {
    override func performKeyEquivalent(with event: NSEvent) -> Bool {
        let commandPressed = event.modifierFlags.contains(.command)
        let key = event.charactersIgnoringModifiers?.lowercased()
        if commandPressed && key == "v" {
            if tryToPerform(#selector(NSText.paste(_:)), with: nil) {
                return true
            }
        }
        return super.performKeyEquivalent(with: event)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    private var window: NSWindow!
    private var webView: PasteAwareWebView!
    private var serverProcess: Process?
    private var outputPipe: Pipe?
    private var serverURL: URL?
    private var outputBuffer = ""

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        createWindow()
        startServer()
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        return true
    }

    func applicationWillTerminate(_ notification: Notification) {
        stopServer()
    }

    private func createWindow() {
        installEditMenu()
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        webView = PasteAwareWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.setValue(false, forKey: "drawsBackground")

        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1320, height: 840),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        window.title = "Worktree Hub"
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.isMovableByWindowBackground = true
        window.minSize = NSSize(width: 900, height: 620)
        window.backgroundColor = NSColor(red: 0.035, green: 0.055, blue: 0.075, alpha: 1)

        let container = NSView(frame: .zero)
        webView.translatesAutoresizingMaskIntoConstraints = false
        container.addSubview(webView)
        NSLayoutConstraint.activate([
            webView.leadingAnchor.constraint(equalTo: container.leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: container.trailingAnchor),
            webView.topAnchor.constraint(equalTo: container.topAnchor),
            webView.bottomAnchor.constraint(equalTo: container.bottomAnchor),
        ])

        // WKWebView consumes background mouse events, so keep a native drag
        // strip above it. Leave the traffic-light area on the left untouched.
        let dragView = WindowDragView(frame: .zero)
        dragView.translatesAutoresizingMaskIntoConstraints = false
        dragView.toolTip = "拖动窗口"
        container.addSubview(dragView)
        NSLayoutConstraint.activate([
            dragView.leadingAnchor.constraint(equalTo: container.leadingAnchor, constant: 78),
            dragView.trailingAnchor.constraint(equalTo: container.trailingAnchor, constant: -12),
            dragView.topAnchor.constraint(equalTo: container.topAnchor),
            dragView.heightAnchor.constraint(equalToConstant: 30),
        ])

        window.contentView = container
        window.center()
        window.makeKeyAndOrderFront(nil)
        showLoading("正在启动本地服务…")
    }

    private func installEditMenu() {
        let mainMenu = NSMenu(title: "主菜单")
        let appMenuItem = NSMenuItem(title: "工作树管家", action: nil, keyEquivalent: "")
        mainMenu.addItem(appMenuItem)
        let editMenuItem = NSMenuItem(title: "编辑", action: nil, keyEquivalent: "")
        let editMenu = NSMenu(title: "编辑")
        editMenu.addItem(withTitle: "剪切", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "复制", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "粘贴", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "全选", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editMenuItem.submenu = editMenu
        mainMenu.addItem(editMenuItem)
        NSApp.mainMenu = mainMenu
    }

    private func showLoading(_ message: String) {
        let escaped = message
            .replacingOccurrences(of: "&", with: "&amp;")
            .replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;")
        let html = """
        <!doctype html><meta charset="utf-8"><style>
        html,body{height:100%;margin:0;background:#090e13;color:#edf5f7;font:14px -apple-system,BlinkMacSystemFont,sans-serif}
        body{display:grid;place-items:center}.box{text-align:center}.mark{width:58px;height:58px;margin:0 auto 20px;display:grid;place-items:center;border-radius:16px;background:#55e6b1;color:#062219;font-weight:900;font-size:18px;box-shadow:0 12px 50px #43d7a438}.copy{color:#8da0aa}
        </style><div class="box"><div class="mark">树</div><strong>工作树管家</strong><p class="copy">\(escaped)</p></div>
        """
        webView.loadHTMLString(html, baseURL: nil)
    }

    private func showError(_ message: String) {
        showLoading("启动失败：\(message)")
    }

    private func startServer() {
        guard let resources = Bundle.main.resourceURL else {
            showError("找不到应用资源")
            return
        }
        let script = resources.appendingPathComponent("worktree_harbor.py")
        guard FileManager.default.fileExists(atPath: script.path) else {
            showError("找不到服务程序")
            return
        }

        let supportBase = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
        let support = supportBase.appendingPathComponent("Worktree Hub", isDirectory: true)
        do {
            try FileManager.default.createDirectory(at: support, withIntermediateDirectories: true)
            let newConfig = support.appendingPathComponent("config.json")
            let legacyConfig = supportBase.appendingPathComponent("Worktree Harbor/config.json")
            if !FileManager.default.fileExists(atPath: newConfig.path),
               FileManager.default.fileExists(atPath: legacyConfig.path) {
                try FileManager.default.copyItem(at: legacyConfig, to: newConfig)
            }
        } catch {
            showError("无法创建配置目录")
            return
        }

        let bundledBackend = resources.appendingPathComponent("worktree_harbor_backend")
        let process = Process()
        let pipe = Pipe()
        if FileManager.default.isExecutableFile(atPath: bundledBackend.path) {
            process.executableURL = bundledBackend
            process.arguments = ["--no-browser", "--host", "127.0.0.1", "--port", "0"]
        } else {
            process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
            process.arguments = [script.path, "--no-browser", "--host", "127.0.0.1", "--port", "0"]
        }
        process.currentDirectoryURL = resources
        process.standardOutput = pipe
        process.standardError = pipe
        process.environment = ProcessInfo.processInfo.environment.merging([
            "PYTHONUNBUFFERED": "1",
            "WORKTREE_HARBOR_DATA_DIR": support.path
        ]) { _, new in new }
        process.terminationHandler = { [weak self] task in
            DispatchQueue.main.async {
                guard let self = self, self.serverProcess === task, self.serverURL == nil else { return }
                self.showError("本地服务已退出（状态 \(task.terminationStatus)）")
            }
        }

        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
            DispatchQueue.main.async { self?.consumeOutput(text) }
        }

        do {
            try process.run()
            serverProcess = process
            outputPipe = pipe
        } catch {
            showError(error.localizedDescription)
        }
    }

    private func consumeOutput(_ text: String) {
        outputBuffer += text
        guard serverURL == nil else { return }
        let pattern = #"http://127\.0\.0\.1:\d+"#
        guard let range = outputBuffer.range(of: pattern, options: .regularExpression),
              let url = URL(string: String(outputBuffer[range])) else { return }
        serverURL = url
        webView.load(URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 30))
    }

    private func stopServer() {
        outputPipe?.fileHandleForReading.readabilityHandler = nil
        if let process = serverProcess, process.isRunning {
            process.terminate()
            let deadline = Date().addingTimeInterval(1.5)
            while process.isRunning && Date() < deadline {
                RunLoop.current.run(until: Date().addingTimeInterval(0.05))
            }
            if process.isRunning { kill(process.processIdentifier, SIGKILL) }
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else {
            decisionHandler(.cancel)
            return
        }
        if url.host == "127.0.0.1" || url.scheme == "about" {
            decisionHandler(.allow)
        } else {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
        }
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
