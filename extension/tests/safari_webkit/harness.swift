// The Safari extension runtime, driven from outside: loads a built extension in WebKit's own WKWebExtensionController (the engine behind
// Safari's web extensions: a background worker, content scripts, chrome.* APIs, the real WebKit fetch rules), opens one page in a tab,
// "clicks the toolbar icon" (WKWebExtensionContext.performAction), and prints what the panel and the chip of the assistant show.
//
//   harness <extension dir> <page url> [seconds] [options]     (see run.py, which builds everything this needs)
//
// Not Safari itself: the scheme of the extension's origin is webkit-extension:// (Safari: safari-web-extension://) and an options page needs
// the context's own web view configuration. It reproduced, before any user could report them, two bugs of the packaged Safari extension that
// no Chromium test can show (a POST whose referrer policy is no-referrer carries `Origin: null`; IndexedDB transactions never answer in the
// worker). Environment: WK_STEPS = a JSON file of [{"sel": "#field", "wait": 20, "key": "tab|enter|esc"}] to focus and press keys after
// pairing.
import Cocoa
import WebKit

func emit(_ s: String) { print(s); fflush(stdout) }

final class WinImpl: NSObject, WKWebExtensionWindow {
    var tab: TabImpl!
    func tabs(for context: WKWebExtensionContext) -> [any WKWebExtensionTab] { [tab] }
    func activeTab(for context: WKWebExtensionContext) -> (any WKWebExtensionTab)? { tab }
    func windowType(for context: WKWebExtensionContext) -> WKWebExtension.WindowType { .normal }
    func windowState(for context: WKWebExtensionContext) -> WKWebExtension.WindowState { .normal }
    func isPrivate(for context: WKWebExtensionContext) -> Bool { false }
}
final class TabImpl: NSObject, WKWebExtensionTab {
    let web: WKWebView
    let win: WinImpl
    init(web: WKWebView, win: WinImpl) { self.web = web; self.win = win }
    func window(for context: WKWebExtensionContext) -> (any WKWebExtensionWindow)? { win }
    func webView(for context: WKWebExtensionContext) -> WKWebView? { web }
    func url(for context: WKWebExtensionContext) -> URL? { web.url }
    func title(for context: WKWebExtensionContext) -> String? { web.title }
    func isSelected(for context: WKWebExtensionContext) -> Bool { true }
    func indexInWindow(for context: WKWebExtensionContext) -> Int { 0 }
    func isLoadingComplete(for context: WKWebExtensionContext) -> Bool { !web.isLoading }
}

final class App: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKExtensionControllerDelegateShim {
    var web: WKWebView!
    var window: NSWindow!
    var ctl: WKWebExtensionController!
    var ctx: WKWebExtensionContext!
    var tab: TabImpl!
    var win: WinImpl!
    var finished = false
    func webView(_ w: WKWebView, didFinish n: WKNavigation!) { finished = true; emit("NAV finish \(w.url?.absoluteString ?? "-")") }
    func webView(_ w: WKWebView, didFail n: WKNavigation!, withError e: Error) { emit("NAV fail \(e)") }
    func webView(_ w: WKWebView, didFailProvisionalNavigation n: WKNavigation!, withError e: Error) { emit("NAV provisional fail \(e)") }
    func webView(_ w: WKWebView, decidePolicyFor r: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) { emit("NAV response \(r.response.url?.absoluteString ?? "-") \((r.response as? HTTPURLResponse)?.statusCode ?? 0) \(r.response.mimeType ?? "-") len=\(r.response.expectedContentLength)"); decisionHandler(.allow) }
    func webViewWebContentProcessDidTerminate(_ w: WKWebView) { emit("NAV content process terminated") }

    func webExtensionController(_ controller: WKWebExtensionController, openWindowsFor extensionContext: WKWebExtensionContext) -> [any WKWebExtensionWindow] { [win] }
    func webExtensionController(_ controller: WKWebExtensionController, focusedWindowFor extensionContext: WKWebExtensionContext) -> (any WKWebExtensionWindow)? { win }

    func js(_ code: String) async -> String {
        do { let r = try await web.evaluateJavaScript(code); return "\(r ?? "nil")" } catch { return "JSERR \(error.localizedDescription)" }
    }
    let panelJS = """
    (()=>{const out=[];for(const el of document.querySelectorAll('*')){if(el.shadowRoot){out.push([...el.shadowRoot.querySelectorAll('*')].filter(e=>e.children.length===0&&e.tagName!=='STYLE').map(e=>(e.value!==undefined&&e.tagName==='INPUT'?'':e.textContent).trim()).filter(Boolean).join(' / ').slice(0,700))}}return out.join(' || ')})()
    """
    let chipJS = """
    (()=>{const out=[];for(const el of document.querySelectorAll('*')){if(el.shadowRoot){for(const c of el.shadowRoot.querySelectorAll('[data-testid="chip"],[data-testid^="chip-status"],[data-testid="chip-value"],[data-testid="chip-why"],[data-testid="chip-trust"]')){out.push((c.getAttribute('data-testid')||'')+': '+c.textContent.replace(/\\s+/g,' ').trim().slice(0,160))}}}return out.join(' | ')})()
    """
    func clickJS(_ label: String) -> String {
        """
        (()=>{for(const el of document.querySelectorAll('*')){if(el.shadowRoot){for(const b of el.shadowRoot.querySelectorAll('button,[role=button]')){if(b.textContent.trim()==='\(label)'){b.click();return 'clicked'}}}}return 'no button \(label)'})()
        """
    }
    func rectJS(_ label: String) -> String {
        """
        (()=>{for(const el of document.querySelectorAll('*')){if(el.shadowRoot){for(const b of el.shadowRoot.querySelectorAll('button,[role=button]')){if(b.textContent.trim()==='\(label)'){const r=b.getBoundingClientRect();return (r.x+r.width/2)+','+(r.y+r.height/2)}}}}return ''})()
        """
    }
    func realClick(_ label: String) async -> String {
        let r = await js(rectJS(label))
        let p = r.split(separator: ",").compactMap { Double($0) }
        guard p.count == 2 else { return "no rect for \(label)" }
        let h = Double(web.bounds.height)
        let pt = NSPoint(x: p[0], y: h - p[1])
        window.makeKeyAndOrderFront(nil)
        for type in [NSEvent.EventType.leftMouseDown, .leftMouseUp] {
            if let ev = NSEvent.mouseEvent(with: type, location: pt, modifierFlags: [], timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: window.windowNumber, context: nil, eventNumber: 0, clickCount: 1, pressure: 1) {
                if type == .leftMouseDown { web.mouseDown(with: ev) } else { web.mouseUp(with: ev) }
            }
        }
        return "real click at \(pt)"
    }
    func clickPoint(_ x: Double, _ y: Double) {
        let pt = NSPoint(x: x, y: Double(web.bounds.height) - y)
        window.makeKeyAndOrderFront(nil)
        for type in [NSEvent.EventType.leftMouseDown, .leftMouseUp] {
            if let ev = NSEvent.mouseEvent(with: type, location: pt, modifierFlags: [], timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: window.windowNumber, context: nil, eventNumber: 0, clickCount: 1, pressure: 1) {
                if type == .leftMouseDown { web.mouseDown(with: ev) } else { web.mouseUp(with: ev) }
            }
        }
    }
    func press(_ name: String) {
        let table: [String: (String, UInt16)] = ["tab": ("\t", 48), "enter": ("\r", 36), "esc": ("\u{1b}", 53)]
        guard let (chars, code) = table[name] else { return }
        for type in [NSEvent.EventType.keyDown, .keyUp] {
            if let ev = NSEvent.keyEvent(with: type, location: .zero, modifierFlags: [], timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: window.windowNumber, context: nil, characters: chars, charactersIgnoringModifiers: chars, isARepeat: false, keyCode: code) {
                if type == .keyDown { web.keyDown(with: ev) } else { web.keyUp(with: ev) }
            }
        }
    }
    func pressTab() {
        for type in [NSEvent.EventType.keyDown, .keyUp] {
            if let ev = NSEvent.keyEvent(with: type, location: .zero, modifierFlags: [], timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: window.windowNumber, context: nil, characters: "\t", charactersIgnoringModifiers: "\t", isARepeat: false, keyCode: 48) {
                if type == .keyDown { web.keyDown(with: ev) } else { web.keyUp(with: ev) }
            }
        }
    }
    let valuesJS = "JSON.stringify([...document.querySelectorAll('input,textarea,select')].filter(e=>e.type!=='hidden'&&e.offsetParent!==null).slice(0,12).map(e=>(e.name||e.id||e.type)+'='+(e.type==='file'?'[file]':e.value)))"
    func fieldRectJS(_ n: Int) -> String { "(()=>{const l=[...document.querySelectorAll('input[type=text],input[type=email],input:not([type]),input[type=tel]')].filter(e=>e.offsetParent!==null);const e=l[\(n)];if(!e)return '';e.scrollIntoView({block:'center'});const r=e.getBoundingClientRect();return (r.x+r.width/2)+','+(r.y+r.height/2)})()" }
    func sleep(_ s: Double) async { try? await Task.sleep(nanoseconds: UInt64(s * 1e9)) }

    func applicationDidFinishLaunching(_ n: Notification) {
        Task { @MainActor in
            do {
                let args = CommandLine.arguments
                let dir = URL(fileURLWithPath: args[1]); let pageURL = URL(string: args[2])!
                let ext = try await WKWebExtension(resourceBaseURL: dir)
                ctx = WKWebExtensionContext(for: ext)
                for p in ext.requestedPermissions { ctx.setPermissionStatus(.grantedExplicitly, for: p) }
                for m in ext.allRequestedMatchPatterns { ctx.setPermissionStatus(.grantedExplicitly, for: m) }
                ctl = WKWebExtensionController()
                ctl.delegate = self
                let cfg = WKWebViewConfiguration()
                cfg.webExtensionController = ctl
                web = WKWebView(frame: NSRect(x: 0, y: 0, width: 1200, height: 900), configuration: cfg)
                web.navigationDelegate = self
                window = NSWindow(contentRect: web.frame, styleMask: [.titled], backing: .buffered, defer: false)
                window.contentView = web
                window.orderFrontRegardless()
                win = WinImpl(); tab = TabImpl(web: web, win: win); win.tab = tab
                try ctl.load(ctx)
                ctl.didOpenWindow(win); ctl.didOpenTab(tab); ctl.didActivateTab(tab, previousActiveTab: nil)
                emit("EVENT loaded errors=\(ctx.errors.map{$0.localizedDescription})")
                await sleep(2)
                if args.count > 4 && args[4] == "options" {
                    try await Task.sleep(nanoseconds: 3_000_000_000)
                    emit("OPTIONS url \(String(describing: ctx.optionsPageURL))")
                    if let o = ctx.optionsPageURL {
                        let ocfg = ctx.webViewConfiguration ?? WKWebViewConfiguration()
                        emit("OPTIONS cfg from ctx: \(ctx.webViewConfiguration != nil)")
                        let ow = WKWebView(frame: NSRect(x: 0, y: 0, width: 900, height: 700), configuration: ocfg)
                        let ownd = NSWindow(contentRect: ow.frame, styleMask: [.titled], backing: .buffered, defer: false)
                        ownd.contentView = ow; ownd.orderFrontRegardless()
                        ow.navigationDelegate = self; ow.load(URLRequest(url: o))
                        try await Task.sleep(nanoseconds: 6_000_000_000)
                        let r = try await ow.evaluateJavaScript("document.body ? document.body.innerText.slice(0,300) : 'NO BODY'")
                        emit("OPTIONS body: \(r ?? "nil")")
                        let r2 = try await ow.evaluateJavaScript("document.documentElement.outerHTML.length")
                        emit("OPTIONS html length \(r2 ?? "nil")")
                    }
                    exit(0)
                }
                web.load(URLRequest(url: pageURL))
                while !finished { await sleep(0.2) }
                emit("EVENT page-loaded")
                await sleep(1)
                ctx.performAction(for: tab)
                emit("EVENT action-performed")
                var last = ""
                var clicked = false
                var typed = false
                let stepsPath = ProcessInfo.processInfo.environment["WK_STEPS"]
                let started = Date()
                let total = Double(args.count > 3 ? args[3] : "60") ?? 60
                while Date().timeIntervalSince(started) < total {
                    await sleep(1)
                    let t = await js(panelJS)
                    let key = t.replacingOccurrences(of: #"Caduca en [0-9:]+"#, with: "", options: .regularExpression)
                    if key != last { emit("PANEL \(t.prefix(220))"); last = key }
                    if !clicked && (t.contains("Conectar") || t.contains("Connect")) {
                        let label = t.contains("Conectar") ? "Conectar" : "Connect"
                        emit("CLICK \(await realClick(label))"); clicked = true
                    }
                    if !typed && stepsPath != nil && (t.contains("Cómo funciona") || t.contains("How it works")) {
                        typed = true
                        let data = try Data(contentsOf: URL(fileURLWithPath: stepsPath!))
                        let steps = try JSONSerialization.jsonObject(with: data) as! [[String: Any]]
                        for step in steps {
                            let sel = step["sel"] as! String
                            let wait = (step["wait"] as? Double) ?? 6
                            _ = await js("(()=>{const e=document.querySelector('\(sel)');e.scrollIntoView({block:'center'});e.focus();return 1})()")
                            var seen = ""
                            let began = Date()
                            while Date().timeIntervalSince(began) < wait {
                                await sleep(1)
                                let now = String((await js(chipJS)).prefix(520))
                                let shown = now.replacingOccurrences(of: #"Caduca en [0-9:]+"#, with: "", options: .regularExpression)
                                if shown != seen { emit("STEP \(sel) PANEL \(now)"); seen = shown }
                            }
                            if let key = step["key"] as? String { press(key); await sleep(2.5) }
                            emit("STEP \(sel) VALUE \(await js("(()=>{const e=document.querySelector('\(sel)');return e.tagName==='SELECT'?e.options[e.selectedIndex].text:e.value})()"))")
                        }
                    }
                    if !typed && t.contains("Cómo funciona") {
                        typed = true
                        emit("VALUES before \(await js(valuesJS))")
                        for n in 0..<3 {
                            let r = await js(fieldRectJS(n)).split(separator: ",").compactMap { Double($0) }
                            guard r.count == 2 else { emit("NO FIELD \(n)"); break }
                            await sleep(0.5)
                            let r2 = await js(fieldRectJS(n)).split(separator: ",").compactMap { Double($0) }
                            clickPoint(r2[0], r2[1])
                            await sleep(4)
                            emit("FIELD \(n) focused=\(await js("document.activeElement && (document.activeElement.name||document.activeElement.id)")) PANEL \((await js(panelJS)).prefix(260))")
                            pressTab()
                            await sleep(2.5)
                            emit("VALUES after tab \(n): \(await js(valuesJS)) active=\(await js("document.activeElement && (document.activeElement.name||document.activeElement.id)"))")
                        }
                    }
                    if FileManager.default.fileExists(atPath: "/tmp/wk_stop") { break }
                }
                emit("EVENT done errors=\(ctx.errors.map{$0.localizedDescription})")
            } catch { emit("FAILED \(error)") }
            exit(0)
        }
    }
}
typealias WKExtensionControllerDelegateShim = WKWebExtensionControllerDelegate
setvbuf(stdout, nil, _IONBF, 0)
let app = NSApplication.shared
let delegate = App()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
