import Cocoa
import SafariServices

// Stand-alone "Meridian Assistant" wrapper app: the containing app of the Safari web extension (appex) when it is built
// on its own, for development, and the only caller SafariServices answers for in that layout. The shipped product embeds
// the same appex in Meridian.app/Contents/PlugIns, where this app does not exist.
//
//   (no arguments)       show a short window explaining how to turn the extension on in Safari
//   --status             print {"installed", "enabled", "error", "extension", "host"} and exit 0 (known to Safari) or 2
//   --open-settings      open Safari > Settings > Extensions with Meridian Assistant selected, exit 0 or 3
//
// SafariServices only answers for the app that contains the extension and not at all for a helper executable inside
// another app, so these modes cannot be called from Meridian.app; Meridian reports the Safari state with pluginkit.

/// Bundle identifier of the embedded Safari web-extension. It must equal CFBundleIdentifier in resources/appex-Info.plist
/// and be prefixed by the identifier of the app that contains it, or Safari refuses to load it.
let extensionIdentifier = "com.meridian.jobagent.assistant.Extension"

enum ExitStatus {
    static let ok: Int32 = 0
    static let notInstalled: Int32 = 2
    static let failed: Int32 = 3
    static let timeout: Int32 = 4
}

/// One JSON object per line on stdout with sorted keys, so the output is stable and easy to assert on.
func emit(_ object: [String: Any]) {
    let data = (try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])) ?? Data("{}".utf8)
    FileHandle.standardOutput.write(data + Data("\n".utf8))
}

/// SafariServices answers asynchronously, and never when the caller is not the extension's containing app.
/// A bounded wait keeps the caller from hanging.
func armWatchdog(seconds: Double) {
    DispatchQueue.global().asyncAfter(deadline: .now() + seconds) {
        emit([
            "extension": extensionIdentifier,
            "host": Bundle.main.bundleIdentifier ?? NSNull(),
            "installed": false,
            "enabled": false,
            "error": "Safari did not answer in time",
        ])
        exit(ExitStatus.timeout)
    }
}

func reportStatusAndExit() {
    armWatchdog(seconds: 10)
    SFSafariExtensionManager.getStateOfSafariExtension(withIdentifier: extensionIdentifier) { state, error in
        emit([
            "extension": extensionIdentifier,
            "host": Bundle.main.bundleIdentifier ?? NSNull(),
            "installed": state != nil,
            "enabled": state?.isEnabled ?? false,
            "error": error.map { String(describing: $0) } ?? NSNull(),
        ])
        exit(state == nil ? ExitStatus.notInstalled : ExitStatus.ok)
    }
}

func openSettingsAndExit() {
    armWatchdog(seconds: 15)
    SFSafariApplication.showPreferencesForExtension(withIdentifier: extensionIdentifier) { error in
        emit([
            "extension": extensionIdentifier,
            "host": Bundle.main.bundleIdentifier ?? NSNull(),
            "opened": error == nil,
            "error": error.map { String(describing: $0) } ?? NSNull(),
        ])
        DispatchQueue.main.async { exit(error == nil ? ExitStatus.ok : ExitStatus.failed) }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!

    func applicationDidFinishLaunching(_ notification: Notification) {
        let arguments = CommandLine.arguments
        if arguments.contains("--status") { reportStatusAndExit(); return }
        if arguments.contains("--open-settings") { openSettingsAndExit(); return }
        let label = NSTextField(wrappingLabelWithString:
            "Meridian Assistant is installed.\nTurn it on in Safari: Settings > Extensions > Meridian Assistant, "
            + "then allow it on the job-application sites you use.")
        let button = NSButton(title: "Open Safari extension settings", target: self, action: #selector(openSettings))
        let stack = NSStackView(views: [label, button])
        stack.orientation = .vertical
        stack.spacing = 16
        stack.edgeInsets = NSEdgeInsets(top: 24, left: 24, bottom: 24, right: 24)
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 460, height: 200), styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        window.title = "Meridian Assistant"
        window.contentView = stack
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc func openSettings() { openSettingsAndExit() }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
let headless = CommandLine.arguments.contains("--status") || CommandLine.arguments.contains("--open-settings")
app.setActivationPolicy(headless ? .accessory : .regular)
app.run()
