import SafariServices
import os.log

/// Required principal class of a Safari web extension. Meridian Assistant does all of its work in JavaScript
/// (the service worker talks to Meridian over loopback), so native messaging only answers a ping and never
/// touches page data, the file system or the network.
@objc(SafariWebExtensionHandler)
final class SafariWebExtensionHandler: NSObject, NSExtensionRequestHandling {
    func beginRequest(with context: NSExtensionContext) {
        os_log(.default, "Meridian Assistant native ping")
        let response = NSExtensionItem()
        response.userInfo = [SFExtensionMessageKey: ["ok": true, "native": "meridian-assistant"]]
        context.completeRequest(returningItems: [response], completionHandler: nil)
    }
}
