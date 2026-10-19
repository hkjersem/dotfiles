// Accessibility helper for scripts/codex-resume.py.
// Usage: swift codex-resume.swift --check | --inspect | --submit [--message TEXT] | --self-test
//
// Talks to the Codex desktop app (shipped as ChatGPT.app, bundle id
// com.openai.codex) through native AXUIElement calls. --check and --inspect never
// activate the app. --submit brings the verified app to the front and focuses the
// verified composer, types "continue" (or --message) if it is empty, then posts one Return.
// If no composer label is recognised, sends only Return to the app's existing
// focus, without typing or claiming confirmation. With --message, a draft or an
// unrecognised composer stops before any input instead. All other discovery failures stop.
// AXPress on Send and setting AXValue both leave the app's own editor state unchanged.
// While any app holds Secure Event Input, the window server refuses automated activation,
// so --submit stops before activating instead of trying to work around it.

import AppKit
import ApplicationServices
import Carbon
import Foundation

// Scheduled modes exit 4 for known transient failures only while nothing was injected.
struct Failure: Error, CustomStringConvertible {
    let description: String
    let retryable: Bool
    let missingComposer: Bool
    let emptyWindow: Bool
    init(_ description: String, retryable: Bool = false, missingComposer: Bool = false, emptyWindow: Bool = false) {
        self.description = description
        self.retryable = retryable
        self.missingComposer = missingComposer
        self.emptyWindow = emptyWindow
    }
}

let bundleIdentifier = "com.openai.codex"
let fallbackNames: Set<String> = ["Codex", "ChatGPT"]
let excludedBundleIdentifiers: Set<String> = ["com.openai.chat"]
// "Arbeid med ChatGPT" is the label observed while a reply is in progress (next to Stopp).
let composerLabels: Set<String> = ["spør chatgpt", "ask chatgpt", "spør codex", "ask codex", "arbeid med chatgpt"]
let sendLabels: Set<String> = ["send", "send prompt", "send message", "send melding"]
let stopLabels: Set<String> = ["stop", "stopp", "stop generating", "stopp generering", "stop streaming"]
// An empty composer shows this voice chat button where Send appears once text is typed.
let voiceLabels: Set<String> = ["start voice chat", "start talechat", "start stemmechat"]
let continueText = "continue"

// Mirrors codex-resume.py: one trimmed line, since a typed newline would send early,
// and no leading slash, which opens the app's command menu.
func validatedMessage(_ text: String) throws -> String {
    let message = text.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !message.isEmpty else { throw Failure("The message is empty.") }
    guard !message.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else {
        throw Failure("The message must be a single line without control characters.")
    }
    guard !message.hasPrefix("/") else { throw Failure("The message must not start with '/'.") }
    return message
}
let maxNodes = 5000
let maxDepth = 64
let maxAncestorLevels = 6
let pollInterval: TimeInterval = 0.1
let enableAttempts = 30
let treeAttempts = 100
let chatAttempts = 100
let treeAttribute = "AXEnhancedUserInterface"
let foregroundAttempts = 20
let confirmAttempts = 50
let scheduledIdleSeconds: TimeInterval = 60
// Allowed gap between the last synthetic key event and the idle clock, which may count it as input.
let injectionTolerance: TimeInterval = 0.1

enum SubmissionMode: String {
    case immediate = "--submit"
    case scheduledInitial = "--scheduled-initial-submit"
    case scheduledRetry = "--scheduled-submit"
    case scheduledFinal = "--scheduled-final-submit"

    var requiredIdle: TimeInterval? { self == .scheduledRetry || self == .scheduledFinal ? scheduledIdleSeconds : nil }
    var scheduled: Bool { self != .immediate }
    // A window without text areas may still be building its tree, so scheduled runs retry before a blind Return.
    var blindReturnWhenEmpty: Bool { self == .immediate || self == .scheduledFinal }
}

protocol UINode: AnyObject {
    func value(_ name: String) throws -> Any?
    func node(_ name: String) throws -> Self?
    func children() throws -> [Self]
    func isSettable(_ name: String) throws -> Bool
    func set(_ name: String, to value: Any) throws
    func same(as other: Self) -> Bool
}

extension UINode {
    func text(_ name: String) throws -> String? {
        guard let raw = try value(name) else { return nil }
        guard let string = raw as? String else { throw Failure("Accessibility attribute \(name) is not text.") }
        return string
    }

    func flag(_ name: String) throws -> Bool? {
        guard let raw = try value(name) else { return nil }
        guard let bool = raw as? Bool else { throw Failure("Accessibility attribute \(name) is not a boolean.") }
        return bool
    }

    func role() throws -> String { try text("AXRole") ?? "" }

    func rawLabel() throws -> String {
        for name in ["AXDescription", "AXTitle"] {
            if let text = try text(name), !isBlank(text) { return text }
        }
        return ""
    }

    func label() throws -> String { normalized(try rawLabel()) }
}

func isBlank(_ text: String) -> Bool { text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
func normalized(_ text: String) -> String { text.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() }

final class AXNode: UINode {
    let element: AXUIElement
    init(_ element: AXUIElement) { self.element = element }

    func value(_ name: String) throws -> Any? {
        var raw: AnyObject?
        let error = AXUIElementCopyAttributeValue(element, name as CFString, &raw)
        switch error {
        case .success: return raw
        case .noValue, .attributeUnsupported: return nil
        default: throw Failure("Reading \(name) failed (AXError \(error.rawValue)).")
        }
    }

    func node(_ name: String) throws -> AXNode? {
        guard let raw = try value(name) else { return nil }
        guard CFGetTypeID(raw as CFTypeRef) == AXUIElementGetTypeID() else {
            throw Failure("Accessibility attribute \(name) is not an element.")
        }
        return AXNode(raw as! AXUIElement)
    }

    func children() throws -> [AXNode] {
        guard let raw = try value("AXChildren") else { return [] }
        guard let elements = raw as? [AXUIElement] else { throw Failure("AXChildren is not a list of elements.") }
        return elements.map(AXNode.init)
    }

    func isSettable(_ name: String) throws -> Bool {
        var settable = DarwinBoolean(false)
        let error = AXUIElementIsAttributeSettable(element, name as CFString, &settable)
        switch error {
        case .success: return settable.boolValue
        case .noValue, .attributeUnsupported: return false
        default: throw Failure("Checking whether \(name) is settable failed (AXError \(error.rawValue)).")
        }
    }

    func set(_ name: String, to value: Any) throws {
        let cfValue: CFTypeRef
        switch value {
        case let string as String: cfValue = string as CFString
        case let bool as Bool: cfValue = bool ? kCFBooleanTrue : kCFBooleanFalse
        default: throw Failure("Unsupported value type for \(name).")
        }
        let error = AXUIElementSetAttributeValue(element, name as CFString, cfValue)
        guard error == .success else { throw Failure("Setting \(name) failed (AXError \(error.rawValue)).") }
    }

    func same(as other: AXNode) -> Bool { CFEqual(element, other.element) }
}

struct Target<N: UINode> {
    let composer: N
    let composerLabel: String
    let placeholder: String
    let send: N?
    let stop: N?
    var voice: N? = nil
}

func collect<N: UINode>(_ root: N, roles: Set<String>) throws -> [N] {
    var found: [N] = []
    var queue: [(N, Int)] = [(root, 0)]
    var index = 0
    while index < queue.count {
        let (node, depth) = queue[index]
        index += 1
        if roles.contains(try node.role()) { found.append(node) }
        let children = try node.children()
        guard children.isEmpty || depth < maxDepth else {
            throw Failure("Accessibility tree is deeper than \(maxDepth) levels; refusing to guess.")
        }
        queue.append(contentsOf: children.map { ($0, depth + 1) })
        guard queue.count <= maxNodes else {
            throw Failure("Accessibility tree has more than \(maxNodes) elements; refusing to guess.")
        }
    }
    return found
}

func focusedWindow<N: UINode>(of app: N) throws -> N {
    guard let window = try app.node("AXFocusedWindow") else {
        throw Failure("The app has no focused window. Open the Codex thread to resume.", retryable: true)
    }
    return window
}

func findTarget<N: UINode>(in window: N) throws -> Target<N> {
    let textAreas = try collect(window, roles: ["AXTextArea"])
    let labelled = try textAreas.filter { composerLabels.contains(try $0.label()) }
    var composers = labelled
    if composers.count > 1 { composers = try composers.filter { try $0.flag("AXFocused") == true } }
    guard composers.count == 1, let composer = composers.first else {
        if labelled.isEmpty {
            // Labels are placeholders, not draft text, so they are safe to report.
            let found = try textAreas.map { "\"" + (try $0.rawLabel()).trimmingCharacters(in: .whitespacesAndNewlines) + "\"" }
            throw Failure("No composer found in the focused window (expected a text area labelled "
                + composerLabels.sorted().joined(separator: ", ") + "; found "
                + (found.isEmpty ? "no text areas" : "text areas labelled " + found.joined(separator: ", ")) + ").",
                retryable: true, missingComposer: true, emptyWindow: textAreas.isEmpty)
        }
        throw Failure("Found \(labelled.count) composers in the focused window and cannot tell which is active.")
    }
    let composerLabel = try composer.label()
    let placeholder = try composer.rawLabel()

    var current = composer
    var nearby: [String] = []
    for _ in 0..<maxAncestorLevels {
        guard let parent = try current.node("AXParent"), !parent.same(as: window) else { break }
        let buttons = try collect(parent, roles: ["AXButton"])
        nearby = try buttons.map { "\"" + (try $0.rawLabel()).trimmingCharacters(in: .whitespacesAndNewlines) + "\"" }
        let sends = try buttons.filter { sendLabels.contains(try $0.label()) }
        let stops = try buttons.filter { stopLabels.contains(try $0.label()) }
        let voices = try buttons.filter { voiceLabels.contains(try $0.label()) }
        if !sends.isEmpty || !stops.isEmpty || !voices.isEmpty {
            guard sends.count <= 1, stops.count <= 1, voices.count <= 1 else {
                throw Failure("Found several Send, Stop or voice chat buttons next to the composer; refusing to guess.")
            }
            return Target(composer: composer, composerLabel: composerLabel, placeholder: placeholder,
                          send: sends.first, stop: stops.first, voice: voices.first)
        }
        current = parent
    }
    // Button labels are interface text, not draft text, so they are safe to report.
    throw Failure("No Send or Stop button found next to the composer (found "
        + (nearby.isEmpty ? "no buttons" : "buttons labelled " + nearby.joined(separator: ", ")) + ").",
        retryable: true)
}

func composerText<N: UINode>(_ target: Target<N>) throws -> String {
    guard let text = try target.composer.text("AXValue") else {
        throw Failure("The composer text is unreadable through Accessibility.")
    }
    // This app reports its placeholder, followed by a newline, as AXValue when the editor is empty.
    // Only surrounding whitespace is ignored; case and inner text must match the label exactly.
    return text.trimmingCharacters(in: .whitespacesAndNewlines) == target.placeholder ? "" : text
}

// Re-resolves the target and refuses to continue if the window or composer changed.
func currentTarget<N: UINode>(app: N, window: N, composer: N) throws -> Target<N> {
    guard let current = try app.node("AXFocusedWindow"), current.same(as: window) else {
        throw Failure("The app's focused window changed; not submitting.", retryable: true)
    }
    let target = try findTarget(in: window)
    guard target.composer.same(as: composer) else {
        throw Failure("The composer changed (another thread may be open); not submitting.", retryable: true)
    }
    return target
}

func verifiedTarget<N: UINode>(app: N, window: N, composer: N, expected: String) throws -> Target<N> {
    let target = try currentTarget(app: app, window: window, composer: composer)
    guard try composerText(target) == expected else {
        throw Failure("The composer text changed while waiting; not submitting.", retryable: true)
    }
    return target
}

// Chromium builds a web view's Accessibility tree only while an assistive client requests it.
// Setting the attribute reports kAXErrorNotImplemented yet takes effect (about 2 s), so its result is ignored.
// Returns a closure that turns it off again when this call turned it on.
func exposeTree<N: UINode>(of app: N, sleep: (TimeInterval) -> Void) throws -> () -> Void {
    if try app.flag(treeAttribute) == true { return {} }
    try? app.set(treeAttribute, to: true)
    _ = try poll(treeAttempts, sleep) {
        guard let window = try app.node("AXFocusedWindow") else { return false }
        return try !collect(window, roles: ["AXTextArea"]).isEmpty
    }
    return { try? app.set(treeAttribute, to: false) }
}

struct Chat {
    let id: String
    let title: String
    // Set for a chat on another Mac the app reaches through remote control.
    var host: String? = nil

    var url: URL {
        get throws {
            let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-_"))
            guard !id.isEmpty, id.unicodeScalars.allSatisfy(allowed.contains) else {
                throw Failure("Invalid chat id \(id.debugDescription).")
            }
            var link = "codex://threads/\(id)"
            if let host {
                guard !host.isEmpty, host.unicodeScalars.allSatisfy(allowed.union(CharacterSet(charactersIn: ":")).contains),
                      let encoded = host.addingPercentEncoding(withAllowedCharacters: allowed) else {
                    throw Failure("Invalid chat host \(host.debugDescription).")
                }
                link += "?hostId=\(encoded)"
            }
            guard let url = URL(string: link) else { throw Failure("Invalid chat id \(id.debugDescription).") }
            return url
        }
    }
}

// The window title shows the chat name, which either side may shorten with an ellipsis.
func titleMatches(_ shown: String?, _ expected: String) -> Bool {
    func clean(_ text: String) -> String { text.split(whereSeparator: \.isWhitespace).joined(separator: " ") }
    func stem(_ text: String) -> String? {
        guard text.hasSuffix("…") else { return nil }
        let stem = String(text.dropLast()).trimmingCharacters(in: .whitespaces)
        return stem.isEmpty ? nil : stem
    }
    guard let shown else { return false }
    let (a, b) = (clean(shown), clean(expected))
    if a == b { return true }
    if let stem = stem(b), a.hasPrefix(stem) { return true }
    if let stem = stem(a), b.hasPrefix(stem) { return true }
    return false
}

// Switches the app to the chat through its link and waits for the window to show it; nothing is typed.
// beforeOpening runs only when a switch is needed, so scheduled retries can require idle time first.
func openChat<N: UINode>(
    _ chat: Chat, in app: N, beforeOpening: () throws -> Void, open: (URL) throws -> Void,
    sleep: (TimeInterval) -> Void
) throws {
    func shown() throws -> String? { try app.node("AXFocusedWindow")?.text("AXTitle") }
    if titleMatches(try shown(), chat.title) { return }
    try beforeOpening()
    try open(try chat.url)
    guard try poll(chatAttempts, sleep, { titleMatches(try shown(), chat.title) }) else {
        throw Failure("Opened chat \(chat.title.debugDescription), but the window shows "
            + "\((try shown() ?? "no title").debugDescription); nothing was typed or sent.", retryable: true)
    }
}

func openLive(_ url: URL) throws {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/open")
    process.arguments = ["-g", url.absoluteString]
    try process.run()
    process.waitUntilExit()
    guard process.terminationStatus == 0 else {
        throw Failure("Could not open \(url.absoluteString) (open exited \(process.terminationStatus)).", retryable: true)
    }
}

func liveSleep(_ seconds: TimeInterval) { RunLoop.current.run(until: Date(timeIntervalSinceNow: seconds)) }

func poll(_ attempts: Int, _ sleep: (TimeInterval) -> Void, _ condition: () throws -> Bool) rethrows -> Bool {
    for attempt in 0...attempts {
        if try condition() { return true }
        if attempt < attempts { sleep(pollInterval) }
    }
    return false
}

enum Outcome { case confirmed, unconfirmed, unverifiedReturn }

// Side effects outside the accessibility tree, injectable for the self-test.
struct Foreground {
    let idleSeconds: () throws -> TimeInterval
    let now: () -> TimeInterval
    let secureInputOwner: () -> String?
    let activate: () throws -> Void
    let isFrontmost: () throws -> Bool
    let typeText: (String) throws -> Void
    // Moves the caret to the end of the composer, then adds a blank line without sending.
    let breakAfterDraft: () throws -> Void
    let pressReturn: () throws -> Void
}

// Lines as the editor reports them, ignoring blank lines and surrounding whitespace.
func squeezed(_ text: String) -> String {
    text.components(separatedBy: .newlines).map { $0.trimmingCharacters(in: .whitespaces) }
        .filter { !$0.isEmpty }.joined(separator: "\n")
}

func requireForeground<N: UINode>(_ composer: N, _ foreground: Foreground) throws {
    guard try composer.flag("AXFocused") == true else {
        throw Failure("The composer lost focus; not typing or pressing Return.", retryable: true)
    }
    guard try foreground.isFrontmost() else {
        throw Failure("Another app came to the front; not typing or pressing Return.", retryable: true)
    }
}

// Synthetic events may reset the idle clock, so after an injection only input newer than it counts as activity.
func activityResumed(idle: TimeInterval, sinceInjection: TimeInterval) -> Bool {
    idle + injectionTolerance < sinceInjection
}

func requireIdle(_ foreground: Foreground, required: TimeInterval, injectedAt: TimeInterval?) throws {
    let idle = try foreground.idleSeconds()
    if let injectedAt {
        let since = foreground.now() - injectedAt
        guard !activityResumed(idle: idle, sinceInjection: min(required, since)) else {
            throw Failure("Keyboard or mouse activity resumed after typing; left the text in the composer and did not press Return.")
        }
    } else if idle < required {
        throw Failure("Keyboard or mouse activity \(Int(idle)) s ago; waiting for \(Int(required)) s without input.", retryable: true)
    }
}

func secureInputFailure(_ owner: String) -> Failure {
    Failure("Secure keyboard entry is active in \(owner), so macOS will not bring the app to the front "
        + "for an automated request; not typing or pressing Return. Close the password prompt or turn off "
        + "Secure Keyboard Entry there, then try again.", retryable: true)
}

func activateForeground(_ foreground: Foreground, requiredIdle: TimeInterval?, sleep: (TimeInterval) -> Void) throws {
    if let requiredIdle { try requireIdle(foreground, required: requiredIdle, injectedAt: nil) }
    if let owner = foreground.secureInputOwner() { throw secureInputFailure(owner) }
    try foreground.activate()
    guard try poll(foregroundAttempts, sleep, { try foreground.isFrontmost() }) else {
        if let owner = foreground.secureInputOwner() { throw secureInputFailure(owner) }
        throw Failure("The app did not come to the front; not typing or pressing Return.", retryable: true)
    }
}

// A chosen chat must still be the one shown, since the user may have switched chats while the helper waited.
func requireChat<N: UINode>(_ window: N, _ title: String?) throws {
    guard let title else { return }
    let shown = try window.text("AXTitle")
    guard titleMatches(shown, title) else {
        throw Failure("The window shows \((shown ?? "no title").debugDescription) instead of chat "
            + "\(title.debugDescription); not typing or pressing Return.", retryable: true)
    }
}

func submitUnverifiedReturn<N: UINode>(
    app: N, window: N, foreground: Foreground, requiredIdle: TimeInterval?, chatTitle: String?,
    sleep: (TimeInterval) -> Void, injected: inout Bool
) throws -> (inserted: Bool, outcome: Outcome) {
    try activateForeground(foreground, requiredIdle: requiredIdle, sleep: sleep)
    if let requiredIdle { try requireIdle(foreground, required: requiredIdle, injectedAt: nil) }
    if let owner = foreground.secureInputOwner() { throw secureInputFailure(owner) }
    guard let current = try app.node("AXFocusedWindow"), current.same(as: window) else {
        throw Failure("The app's focused window changed; not pressing Return.", retryable: true)
    }
    guard try foreground.isFrontmost() else {
        throw Failure("Another app came to the front; not pressing Return.", retryable: true)
    }
    try requireChat(window, chatTitle)
    injected = true
    try foreground.pressReturn()
    return (false, .unverifiedReturn)
}

// requiredIdle gates activation, typing and Return on seconds without keyboard or mouse input (scheduled retries).
// After the first injected key, every failure is permanent so a retry can never duplicate or erase input.
func submit<N: UINode>(
    app: N, foreground: Foreground, requiredIdle: TimeInterval? = nil, message: String? = nil,
    chatTitle: String? = nil, blindReturnWhenEmpty: Bool = true, sleep: (TimeInterval) -> Void
) throws -> (inserted: Bool, outcome: Outcome) {
    var injected = false
    do {
        return try submitSteps(app: app, foreground: foreground, requiredIdle: requiredIdle, message: message,
                               chatTitle: chatTitle, blindReturnWhenEmpty: blindReturnWhenEmpty, sleep: sleep,
                               injected: &injected)
    } catch let failure as Failure where injected && failure.retryable {
        throw Failure(failure.description)
    }
}

func submitSteps<N: UINode>(
    app: N, foreground: Foreground, requiredIdle: TimeInterval?, message: String?, chatTitle: String?,
    blindReturnWhenEmpty: Bool, sleep: (TimeInterval) -> Void, injected: inout Bool
) throws -> (inserted: Bool, outcome: Outcome) {
    let text = message ?? continueText
    let window = try focusedWindow(of: app)
    let initial: Target<N>
    do {
        initial = try findTarget(in: window)
    } catch let failure as Failure where failure.missingComposer {
        // The Return-only fallback cannot type, so it would drop a custom message.
        guard message == nil else {
            throw Failure(failure.description + " A custom message needs a recognised composer; nothing was typed or sent.",
                          retryable: true)
        }
        guard !failure.emptyWindow || blindReturnWhenEmpty else {
            throw Failure(failure.description + " Retrying before pressing Return without a recognised composer.",
                          retryable: true)
        }
        return try submitUnverifiedReturn(app: app, window: window, foreground: foreground, requiredIdle: requiredIdle,
                                          chatTitle: chatTitle, sleep: sleep, injected: &injected)
    }
    guard try initial.composer.flag("AXEnabled") != false else {
        throw Failure("The composer is disabled (a remote Mac may be disconnected); not submitting.", retryable: true)
    }
    let composer = initial.composer
    let original = try composerText(initial)
    let inserted = isBlank(original)
    // Send replaces the voice chat button once text is typed, which the wait after typing confirms.
    guard initial.send != nil || (initial.voice != nil && initial.stop == nil && inserted) else {
        throw Failure(initial.stop != nil
            ? "A reply appears to be in progress (Stop is shown); not submitting."
            : "No Send button next to the composer; not submitting.", retryable: true)
    }
    // A custom message goes after a draft, separated by a blank line, so both are sent together.
    let appended = message != nil && !inserted

    // An empty composer keeps Send disabled until text is typed, so only a draft waits for it here.
    if !inserted {
        let enabled = try poll(enableAttempts, sleep) {
            try verifiedTarget(app: app, window: window, composer: composer, expected: original).send?.flag("AXEnabled") == true
        }
        guard enabled else { throw Failure("Send stayed disabled or unavailable; not submitting.", retryable: true) }
    }
    guard try composer.isSettable("AXFocused") else {
        throw Failure("The composer cannot be focused through Accessibility; not typing or pressing Return.")
    }

    try activateForeground(foreground, requiredIdle: requiredIdle, sleep: sleep)
    try composer.set("AXFocused", to: true)
    guard try poll(foregroundAttempts, sleep, { try composer.flag("AXFocused") == true }) else {
        throw Failure("Could not confirm focus on the composer; not typing or pressing Return.", retryable: true)
    }

    var expected = original
    var injectedAt: TimeInterval?
    if inserted || appended {
        _ = try verifiedTarget(app: app, window: window, composer: composer, expected: original)
        try requireForeground(composer, foreground)
        if let requiredIdle { try requireIdle(foreground, required: requiredIdle, injectedAt: nil) }
        try requireChat(window, chatTitle)
        injected = true
        if appended { try foreground.breakAfterDraft() }
        try foreground.typeText(text)
        injectedAt = foreground.now()
        // Typed characters arrive asynchronously; never undo them, since the editor may hold user edits by now.
        let goal = squeezed(appended ? original + "\n\n" + text : text)
        var typed = ""
        let ready = try poll(enableAttempts, sleep) {
            let target = try currentTarget(app: app, window: window, composer: composer)
            typed = try composerText(target)
            let shown = squeezed(typed)
            if shown == goal { return try target.send?.flag("AXEnabled") == true }
            guard goal.hasPrefix(shown) else {
                throw Failure("The composer holds unexpected text after typing; left it as is and did not press Return.")
            }
            return false
        }
        guard ready else {
            throw Failure(squeezed(typed) == goal
                ? "Typed \(message == nil ? "\"\(continueText)\"" : "the custom message") but Send stayed disabled; left it in the composer and did not press Return."
                : "The typed text did not fully appear in the composer; left it as is and did not press Return.")
        }
        expected = typed
    }

    // Last check before the keystroke. Send can be disabled briefly while the window takes focus.
    let sendReady = try poll(enableAttempts, sleep) {
        try verifiedTarget(app: app, window: window, composer: composer, expected: expected).send?.flag("AXEnabled") == true
    }
    guard sendReady else {
        throw Failure("Send became disabled or unavailable; not pressing Return.", retryable: true)
    }
    try requireForeground(composer, foreground)
    if let requiredIdle { try requireIdle(foreground, required: requiredIdle, injectedAt: injectedAt) }
    try requireChat(window, chatTitle)
    injected = true
    try foreground.pressReturn()

    for _ in 0..<confirmAttempts {
        sleep(pollInterval)
        // After Return the UI may be re-rendering, so a failed read only means "not yet confirmed".
        do {
            let target = try findTarget(in: window)
            if target.stop != nil { return (inserted, .confirmed) }
            if isBlank(try composerText(target)) { return (inserted, .confirmed) }
        } catch is Failure {
            continue
        }
    }
    return (inserted, .unconfirmed)
}

// MARK: - Live app

func liveApp() throws -> (NSRunningApplication, AXNode) {
    guard AXIsProcessTrusted() else {
        throw Failure("This process lacks Accessibility permission (System Settings > Privacy & Security > Accessibility).")
    }
    var candidates = NSRunningApplication.runningApplications(withBundleIdentifier: bundleIdentifier)
        .filter { $0.activationPolicy == .regular }
    if candidates.isEmpty {
        candidates = NSWorkspace.shared.runningApplications.filter {
            $0.activationPolicy == .regular
                && fallbackNames.contains($0.localizedName ?? "")
                && !excludedBundleIdentifiers.contains($0.bundleIdentifier ?? "")
        }
    }
    guard candidates.count == 1, let app = candidates.first else {
        throw (candidates.isEmpty
            ? Failure("The Codex app is not running.", retryable: true)
            : Failure("Several Codex app instances are running; refusing to guess."))
    }
    let element = AXUIElementCreateApplication(app.processIdentifier)
    AXUIElementSetMessagingTimeout(element, 3)
    return (app, AXNode(element))
}

func printJSON(_ object: [String: Any]) throws {
    let data = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys, .prettyPrinted])
    print(String(decoding: data, as: UTF8.self))
}

func jsonValue(_ flag: Bool?) -> Any {
    if let flag { return flag }
    return NSNull()
}

func check(chat: Chat?) throws {
    let (app, node) = try liveApp()
    if let chat { try openChat(chat, in: node, beforeOpening: {}, open: openLive, sleep: liveSleep) }
    let restoreTree = try exposeTree(of: node, sleep: liveSleep)
    defer { restoreTree() }
    let window = try focusedWindow(of: node)
    let target = try findTarget(in: window)
    let sendEnabled = try target.send?.flag("AXEnabled") == true
    let idle = try hardwareIdleSeconds()
    try printJSON([
        "app": [
            "bundleIdentifier": app.bundleIdentifier ?? "",
            "frontmost": app.isActive,
            "name": app.localizedName ?? "",
            "frontmostSettable": try node.isSettable("AXFrontmost"),
            "pid": Int(app.processIdentifier),
        ],
        "composer": [
            "enabled": jsonValue(try target.composer.flag("AXEnabled")),
            "focusSettable": try target.composer.isSettable("AXFocused"),
            "focused": jsonValue(try target.composer.flag("AXFocused")),
            "label": target.composerLabel,
        ],
        "send": ["enabled": sendEnabled, "found": target.send != nil],
        "stop": ["found": target.stop != nil],
        "voice": ["found": target.voice != nil],
        "readyToSubmit": target.send != nil && sendEnabled,
        "secureInputOwner": secureInputOwner() ?? NSNull(),
        "idleSeconds": Int(idle),
        "idleEligible": idle >= scheduledIdleSeconds,
        "windowTitle": try window.text("AXTitle") ?? NSNull(),
    ])
}

func inspect(chat: Chat?) throws {
    let (_, node) = try liveApp()
    if let chat { try openChat(chat, in: node, beforeOpening: {}, open: openLive, sleep: liveSleep) }
    let restoreTree = try exposeTree(of: node, sleep: liveSleep)
    defer { restoreTree() }
    let window = try focusedWindow(of: node)
    let text = try composerText(try findTarget(in: window))
    try printJSON(["empty": isBlank(text), "value": text, "windowTitle": try window.text("AXTitle") ?? NSNull()])
}

// The session dictionary names the app responsible for Secure Event Input; the Carbon flag covers a missing key.
func secureInputOwner() -> String? {
    let session = CGSessionCopyCurrentDictionary() as? [String: Any]
    if let pid = (session?["kCGSSessionSecureInputPID"] as? NSNumber)?.int32Value, pid > 0 {
        let name = NSRunningApplication(processIdentifier: pid)?.localizedName ?? "an app"
        return "\(name) (pid \(pid))"
    }
    return IsSecureEventInputEnabled() ? "an unidentified app" : nil
}

// Seconds since the last keyboard, mouse or trackpad event in the login session.
func hardwareIdleSeconds() throws -> TimeInterval {
    guard let anyInput = CGEventType(rawValue: ~0) else { throw Failure("Could not build the any-input event type.") }
    let idle = CGEventSource.secondsSinceLastEventType(.hidSystemState, eventType: anyInput)
    guard idle.isFinite, idle >= 0 else { throw Failure("The idle time is unavailable (\(idle)); not submitting.") }
    return idle
}

// NSWorkspace updates frontmostApplication from run-loop notifications, so drain pending ones first.
// The system-wide AXFocusedApplication query fails with AXError -25204 here.
func frontmostPid() -> pid_t? {
    _ = RunLoop.current.run(mode: .default, before: Date())
    return NSWorkspace.shared.frontmostApplication?.processIdentifier
}

func keyEvents(_ source: CGEventSource, key: CGKeyCode, text: String? = nil) throws -> [CGEvent] {
    guard let down = CGEvent(keyboardEventSource: source, virtualKey: key, keyDown: true),
          let up = CGEvent(keyboardEventSource: source, virtualKey: key, keyDown: false) else {
        throw Failure("Could not create a key event.")
    }
    for event in [down, up] {
        event.flags = []
        if let text {
            let units = Array(text.utf16)
            event.keyboardSetUnicodeString(stringLength: units.count, unicodeString: units)
        }
    }
    return [down, up]
}

// Types one character at a time and stops as soon as another app is frontmost.
// With watchIdle, it also stops when keyboard or mouse input arrives between characters.
func typeLive(_ text: String, pid: pid_t, watchIdle: Bool) throws {
    guard let source = CGEventSource(stateID: .hidSystemState) else { throw Failure("Could not create a key event source.") }
    let characters = Array(text)
    var postedAt: Date?
    for (index, character) in characters.enumerated() {
        guard frontmostPid() == pid else {
            throw Failure("Another app came to the front after typing \(index) of \(characters.count) characters; stopped and did not press Return.")
        }
        if watchIdle, let postedAt,
           activityResumed(idle: try hardwareIdleSeconds(), sinceInjection: Date().timeIntervalSince(postedAt)) {
            throw Failure("Keyboard or mouse activity resumed after typing \(index) of \(characters.count) characters; stopped and did not press Return.")
        }
        for event in try keyEvents(source, key: 0, text: String(character)) { event.post(tap: .cgSessionEventTap) }
        postedAt = Date()
        RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.02))
    }
}

// Posts one Return through the session event tap, the same path System Events uses.
// Callers verify the app is frontmost; the Enter-only fallback preserves its existing focus.
func postReturn() throws { try postKey(0x24) }

// Cmd+Down moves to the end of the editor and Shift+Return adds a line without sending, both verified live.
func postKey(_ key: CGKeyCode, flags: CGEventFlags = []) throws {
    guard let source = CGEventSource(stateID: .hidSystemState) else { throw Failure("Could not create a key event source.") }
    for event in try keyEvents(source, key: key) {
        event.flags = flags
        event.post(tap: .cgSessionEventTap)
    }
    RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.1))
}

func submitLive(mode: SubmissionMode, message: String?, chat: Chat?) throws -> Int32 {
    let requiredIdle = mode.requiredIdle
    let (runningApp, node) = try liveApp()
    let pid = runningApp.processIdentifier
    let previous = frontmostPid()
    let foreground = Foreground(
        idleSeconds: hardwareIdleSeconds,
        now: { Date().timeIntervalSinceReferenceDate },
        secureInputOwner: secureInputOwner,
        activate: {
            do {
                try node.set("AXFrontmost", to: true)
            } catch let failure as Failure {
                guard runningApp.activate() else { throw Failure("Activating the app failed: \(failure)", retryable: true) }
            }
        },
        isFrontmost: {
            guard let frontmost = frontmostPid() else { throw Failure("Could not read the frontmost app; not typing or pressing Return.") }
            return frontmost == pid
        },
        typeText: { try typeLive($0, pid: pid, watchIdle: requiredIdle != nil) },
        breakAfterDraft: {
            guard frontmostPid() == pid else { throw Failure("Another app came to the front; did not add the message.") }
            try postKey(0x7D, flags: .maskCommand)
            try postKey(0x24, flags: .maskShift)
            try postKey(0x24, flags: .maskShift)
        },
        pressReturn: postReturn
    )
    defer {
        if let previous, previous != pid {
            do {
                if frontmostPid() == pid {
                    try AXNode(AXUIElementCreateApplication(previous)).set("AXFrontmost", to: true)
                }
            } catch {
                FileHandle.standardError.write(Data("Could not return focus to the previous app: \(error)\n".utf8))
            }
        }
    }
    if let chat {
        try openChat(chat, in: node, beforeOpening: {
            if let requiredIdle { try requireIdle(foreground, required: requiredIdle, injectedAt: nil) }
        }, open: openLive, sleep: liveSleep)
    }
    let restoreTree = try exposeTree(of: node, sleep: liveSleep)
    defer { restoreTree() }
    let result = try submit(app: node, foreground: foreground, requiredIdle: requiredIdle, message: message,
                            chatTitle: chat?.title, blindReturnWhenEmpty: mode.blindReturnWhenEmpty, sleep: liveSleep)
    let typed = message == nil ? "typed \"\(continueText)\"" : "typed the custom message"
    let draft = result.inserted ? typed : message == nil ? "kept the existing draft" : "added the custom message after the draft"
    switch result.outcome {
    case .confirmed:
        print("Pressed Return in the composer (\(draft)); the composer cleared or a reply started.")
        return 0
    case .unconfirmed:
        FileHandle.standardError.write(Data("Pressed Return in the composer (\(draft)), but could not confirm the submission; check the app.\n".utf8))
        return 3
    case .unverifiedReturn:
        FileHandle.standardError.write(Data("Composer not recognised; pressed Return once without typing text. Submission cannot be confirmed; check the app before retrying.\n".utf8))
        return 3
    }
}

// Exit 4 tells the scheduler that nothing was injected and a later attempt may succeed.
func exitCode(for error: Error, scheduled: Bool) -> Int32 {
    scheduled && (error as? Failure)?.retryable == true ? 4 : 1
}

// MARK: - Self-test with an in-memory tree

final class FakeNode: UINode {
    var attributes: [String: Any]
    var kids: [FakeNode] = []
    weak var up: FakeNode?
    var settable: Set<String> = []
    var ignoredSets: Set<String> = []
    var onSet: ((String, Any) -> Void)?
    var sets: [String] = []

    init(_ role: String, _ attributes: [String: Any] = [:], _ children: [FakeNode] = []) {
        self.attributes = attributes
        self.attributes["AXRole"] = role
        add(children)
    }

    func add(_ children: [FakeNode]) {
        for child in children { child.up = self }
        kids += children
    }

    var valueSets: [String] { sets.filter { $0.hasPrefix("AXValue=") }.map { String($0.dropFirst(8)) } }

    func value(_ name: String) throws -> Any? { attributes[name] }
    func node(_ name: String) throws -> FakeNode? { name == "AXParent" ? up : attributes[name] as? FakeNode }
    func children() throws -> [FakeNode] { kids }
    func isSettable(_ name: String) throws -> Bool { settable.contains(name) }

    func set(_ name: String, to value: Any) throws {
        guard settable.contains(name) else { throw Failure("fake: \(name) not settable") }
        sets.append("\(name)=\(value)")
        if !ignoredSets.contains(name) { attributes[name] = value }
        onSet?(name, value)
    }

    func same(as other: FakeNode) -> Bool { self === other }
}

// Models the window server: keys reach the composer only when the app is frontmost and the composer focused.
// Typed text replaces the placeholder and, like the real editor, enables Send.
final class FakeHost {
    var frontmost = false
    var frontmostReadable = true
    var activates = true
    var activateCalls = 0
    var onActivate: (() -> Void)?
    var secureInputOwner: String?
    var secureInputAfterActivation: String?
    var typingAccepted = true
    var typingEnablesSend = true
    var typed: [(frontmost: Bool, focused: Bool, before: String?, text: String)] = []
    var onType: (() -> Void)?
    var returns: [(frontmost: Bool, focused: Bool, text: String?)] = []
    var onReturn: (() -> Void)?
    // Idle clock: the last input is user activity or, as the worst case, a synthetic key we typed.
    var clock: TimeInterval = 1000
    var lastInput: TimeInterval = 0
    var idleReadable = true
    var typeError: Failure?
    var returnError: Failure?
    var breaks: [(frontmost: Bool, focused: Bool)] = []

    func userActivity(secondsAgo: TimeInterval = 0) { lastInput = clock - secondsAgo }

    func foreground(_ f: Fixture) -> Foreground {
        Foreground(
            idleSeconds: {
                guard self.idleReadable else { throw Failure("fake: idle unavailable") }
                return self.clock - self.lastInput
            },
            now: { self.clock },
            secureInputOwner: { self.activateCalls == 0 ? self.secureInputOwner : self.secureInputAfterActivation },
            activate: {
                self.activateCalls += 1
                if self.activates { self.frontmost = true }
                self.onActivate?()
            },
            isFrontmost: {
                guard self.frontmostReadable else { throw Failure("fake: frontmost app unreadable") }
                return self.frontmost
            },
            typeText: { text in
                let focused = f.composer.attributes["AXFocused"] as? Bool == true
                let before = f.composer.attributes["AXValue"] as? String
                self.typed.append((self.frontmost, focused, before, text))
                self.lastInput = self.clock
                if let error = self.typeError { throw error }
                guard self.frontmost && focused && self.typingAccepted else { return }
                let isPlaceholder = before?.trimmingCharacters(in: .whitespacesAndNewlines) == "Spør ChatGPT"
                f.composer.attributes["AXValue"] = (isPlaceholder ? "" : before ?? "") + text
                if self.typingEnablesSend { f.send.attributes["AXEnabled"] = true }
                self.onType?()
            },
            breakAfterDraft: {
                let focused = f.composer.attributes["AXFocused"] as? Bool == true
                self.breaks.append((self.frontmost, focused))
                self.lastInput = self.clock
                guard self.frontmost && focused else { return }
                f.composer.attributes["AXValue"] = (f.composer.attributes["AXValue"] as? String ?? "") + "\n\n"
            },
            pressReturn: {
                let focused = f.composer.attributes["AXFocused"] as? Bool == true
                self.returns.append((self.frontmost, focused, f.composer.attributes["AXValue"] as? String))
                self.lastInput = self.clock
                if let error = self.returnError { throw error }
                if self.frontmost && focused { self.onReturn?() }
            }
        )
    }
}

struct Fixture {
    let app: FakeNode, window: FakeNode, composer: FakeNode, send: FakeNode, toolbar: FakeNode, sidebar: FakeNode
    let host = FakeHost()
}

func button(_ label: String, enabled: Bool = true) -> FakeNode {
    FakeNode("AXButton", ["AXDescription": label, "AXEnabled": enabled])
}

func fixture(draft: String? = "keep going", sendEnabled: Bool = true) -> Fixture {
    var attributes: [String: Any] = ["AXDescription": "Spør ChatGPT", "AXFocused": false, "AXEnabled": true]
    if let draft { attributes["AXValue"] = draft }
    let composer = FakeNode("AXTextArea", attributes)
    composer.settable = ["AXFocused"]
    let send = button("Send", enabled: sendEnabled)
    let toolbar = FakeNode("AXGroup", [:], [button("Diktat"), send])
    let box = FakeNode("AXGroup", [:], [FakeNode("AXGroup", [:], [composer]), toolbar])
    let sidebar = FakeNode("AXGroup", [:], [button("Fest chat"), button("project"), FakeNode("AXTextArea", ["AXValue": "x"])])
    let window = FakeNode("AXWindow", [:], [sidebar, FakeNode("AXGroup", [:], [box])])
    let app = FakeNode("AXApplication", ["AXFocusedWindow": window], [window])
    let f = Fixture(app: app, window: window, composer: composer, send: send, toolbar: toolbar, sidebar: sidebar)
    f.host.onReturn = { composer.attributes["AXValue"] = "" }
    return f
}

func selfTest() -> Int32 {
    var failures: [String] = []
    var count = 0

    func test(_ name: String, _ body: () throws -> Void) {
        count += 1
        do { try body() } catch { failures.append("\(name): \(error)") }
    }
    func expect(_ condition: Bool, _ message: String) throws {
        if !condition { throw Failure(message) }
    }
    func expectFailure(_ fragment: String, _ body: () throws -> Void) throws {
        do { try body() } catch let failure as Failure {
            try expect(failure.description.contains(fragment), "unexpected error: \(failure)")
            return
        }
        throw Failure("expected an error containing \"\(fragment)\"")
    }
    func run(_ f: Fixture, sleep: (TimeInterval) -> Void = { _ in }) throws -> (inserted: Bool, outcome: Outcome) {
        try submit(app: f.app, foreground: f.host.foreground(f), sleep: sleep)
    }
    func runScheduled(_ f: Fixture, sleep: ((TimeInterval) -> Void)? = nil) throws -> (inserted: Bool, outcome: Outcome) {
        try submit(app: f.app, foreground: f.host.foreground(f), requiredIdle: scheduledIdleSeconds,
                   sleep: sleep ?? { f.host.clock += $0 })
    }
    // Returns the failure so callers can check whether a scheduled retry would be allowed.
    func failure(_ fragment: String, _ body: () throws -> Void) throws -> Failure {
        do { try body() } catch let failure as Failure {
            try expect(failure.description.contains(fragment), "unexpected error: \(failure)")
            return failure
        }
        throw Failure("expected an error containing \"\(fragment)\"")
    }
    func expectNoReturn(_ f: Fixture, typed: Int = 0) throws {
        try expect(f.host.returns.isEmpty, "pressed Return")
        try expect(f.host.typed.count == typed, "typed \(f.host.typed.count) times, expected \(typed)")
        try expect(f.composer.valueSets.isEmpty, "set AXValue")
    }

    test("selects the labelled composer and its Send, ignoring sidebar") {
        let f = fixture()
        let target = try findTarget(in: f.window)
        try expect(target.composer === f.composer && target.send === f.send && target.stop == nil, "wrong target")
    }
    test("prefers the focused composer among several") {
        let f = fixture()
        f.composer.attributes["AXFocused"] = true
        let other = FakeNode("AXTextArea", ["AXDescription": "Ask ChatGPT", "AXFocused": false])
        f.window.add([FakeNode("AXGroup", [:], [FakeNode("AXGroup", [:], [other]), FakeNode("AXGroup", [:], [button("Send")])])])
        try expect(try findTarget(in: f.window).composer === f.composer, "picked unfocused composer")
    }
    test("refuses several focused composers") {
        let f = fixture()
        f.composer.attributes["AXFocused"] = true
        let other = FakeNode("AXTextArea", ["AXDescription": "Ask ChatGPT", "AXFocused": true])
        f.window.add([FakeNode("AXGroup", [:], [other])])
        try expectFailure("cannot tell which") { _ = try findTarget(in: f.window) }
    }
    test("refuses unlabelled text areas") {
        let f = fixture()
        f.composer.attributes["AXDescription"] = "Notes"
        try expectFailure("found text areas labelled \"\", \"Notes\").") { _ = try findTarget(in: f.window) }
    }
    test("retries a window without text areas until the final scheduled attempt, then presses Return blindly") {
        for final in [false, true] {
            let f = fixture()
            f.sidebar.kids.removeAll { (try? $0.role()) == "AXTextArea" }
            f.composer.up?.kids.removeAll()
            if final {
                let result = try submit(app: f.app, foreground: f.host.foreground(f), requiredIdle: scheduledIdleSeconds,
                                        blindReturnWhenEmpty: true, sleep: { _ in })
                try expect(result.outcome == .unverifiedReturn && f.host.returns.count == 1, "no blind Return at the end")
            } else {
                let error = try failure("Retrying before pressing Return") {
                    _ = try submit(app: f.app, foreground: f.host.foreground(f), requiredIdle: scheduledIdleSeconds,
                                   blindReturnWhenEmpty: false, sleep: { _ in })
                }
                try expect(exitCode(for: error, scheduled: true) == 4, "not retryable")
                try expect(f.host.activateCalls == 0, "activated the app")
                try expectNoReturn(f)
            }
        }
        try expect(SubmissionMode.immediate.blindReturnWhenEmpty && SubmissionMode.scheduledFinal.blindReturnWhenEmpty
            && !SubmissionMode.scheduledInitial.blindReturnWhenEmpty && !SubmissionMode.scheduledRetry.blindReturnWhenEmpty,
            "wrong blind-Return modes")
        try expect(SubmissionMode.scheduledFinal.requiredIdle == scheduledIdleSeconds, "final attempt ignores idle")
    }
    test("opens a chat through its link once idle and waits for its title") {
        let f = fixture()
        f.window.attributes["AXTitle"] = "Other chat"
        let chat = Chat(id: "01a1-x", title: "Target chat")
        var opened: [URL] = []
        var checks = 0
        var naps = 0
        try openChat(chat, in: f.app, beforeOpening: { checks += 1 }, open: { opened.append($0) }) { _ in
            naps += 1
            if naps == 2 { f.window.attributes["AXTitle"] = "Target chat" }
        }
        try expect(opened.map(\.absoluteString) == ["codex://threads/01a1-x"] && checks == 1, "did not open once: \(opened)")
        try openChat(chat, in: f.app, beforeOpening: { checks += 1 }, open: { opened.append($0) }) { _ in }
        try expect(opened.count == 1 && checks == 1, "reopened a chat that was already shown")
    }
    test("does not switch chats while the idle check fails, and stops on the wrong title") {
        let f = fixture()
        f.window.attributes["AXTitle"] = "Other chat"
        var opened = 0
        let busy = try failure("activity") {
            try openChat(Chat(id: "a", title: "Target"), in: f.app,
                         beforeOpening: { throw Failure("Keyboard or mouse activity 3 s ago", retryable: true) },
                         open: { _ in opened += 1 }) { _ in }
        }
        try expect(opened == 0 && busy.retryable, "switched chats while the user was active")
        var naps = 0
        let wrong = try failure("window shows \"Other chat\"") {
            try openChat(Chat(id: "a", title: "Target"), in: f.app, beforeOpening: {}, open: { _ in opened += 1 }) { _ in naps += 1 }
        }
        try expect(opened == 1 && naps == chatAttempts && wrong.retryable, "did not give up after the wait")
        try expectNoReturn(f)
        try expectFailure("Invalid chat id") { _ = try Chat(id: "a/../b", title: "x").url }
        try expectFailure("Invalid chat host") { _ = try Chat(id: "a", title: "x", host: "h&x=1").url }
        let remote = try Chat(id: "01a1-x", title: "x", host: "remote-control:env_e_1").url.absoluteString
        try expect(remote == "codex://threads/01a1-x?hostId=remote-control%3Aenv_e_1", "remote link: \(remote)")
    }
    test("matches window titles shortened with an ellipsis") {
        try expect(titleMatches("Find  current\nstatus", "Find current status"), "whitespace")
        try expect(titleMatches("im thinking of introducing light", "im thinking of introducing…"), "expected shortened")
        try expect(titleMatches("im thinking…", "im thinking of introducing light"), "shown shortened")
        try expect(!titleMatches("Guardian review", "Find current status") && !titleMatches(nil, "x")
            && !titleMatches("anything", "…"), "false match")
    }
    test("exposes the Accessibility tree on demand and turns it off again") {
        let window = FakeNode("AXWindow")
        let app = FakeNode("AXApplication", ["AXFocusedWindow": window, treeAttribute: false], [window])
        app.settable = [treeAttribute]
        var naps = 0
        app.onSet = { _, value in
            if value as? Bool == false { window.kids.removeAll() }
        }
        let restore = try exposeTree(of: app) { _ in
            naps += 1
            if naps == 3 { window.add([FakeNode("AXTextArea")]) }
        }
        try expect(app.sets == ["\(treeAttribute)=true"] && naps == 3, "did not wait for the tree: \(app.sets), \(naps)")
        restore()
        try expect(app.sets.last == "\(treeAttribute)=false" && window.kids.isEmpty, "did not turn it off")
    }
    test("leaves an already exposed tree alone and gives up after the wait") {
        let window = FakeNode("AXWindow")
        let on = FakeNode("AXApplication", ["AXFocusedWindow": window, treeAttribute: true], [window])
        on.settable = [treeAttribute]
        try exposeTree(of: on) { _ in }()
        try expect(on.sets.isEmpty, "changed an exposed tree")
        let off = FakeNode("AXApplication", ["AXFocusedWindow": window, treeAttribute: false], [window])
        off.settable = [treeAttribute]
        var naps = 0
        try exposeTree(of: off) { _ in naps += 1 }()
        try expect(naps == treeAttempts && off.sets.count == 2, "unexpected wait \(naps) or sets \(off.sets)")
    }
    test("refuses two Send buttons next to the composer") {
        let f = fixture()
        f.toolbar.add([button("Send")])
        try expectFailure("several Send") { _ = try findTarget(in: f.window) }
    }
    test("does not borrow a Send button from outside the composer region") {
        let f = fixture()
        f.send.attributes["AXDescription"] = "Other"
        f.sidebar.add([button("Send")])
        try expectFailure("No Send or Stop") { _ = try findTarget(in: f.window) }
    }
    test("types into an empty composer that shows the voice chat button instead of Send") {
        let f = fixture(draft: "", sendEnabled: false)
        f.send.attributes["AXDescription"] = "Start talechat"
        f.host.onType = { f.send.attributes["AXDescription"] = "Send" }
        let result = try run(f)
        try expect(result.inserted && result.outcome == .confirmed, "unexpected result \(result)")
        try expect(f.host.returns.count == 1, "Return not pressed once")
    }
    test("does not press Return when Send never replaces the voice chat button") {
        let f = fixture(draft: "", sendEnabled: false)
        f.send.attributes["AXDescription"] = "Start talechat"
        try expectFailure("Send stayed disabled; left it in the composer") { _ = try run(f) }
        try expectNoReturn(f, typed: 1)
    }
    test("refuses a draft next to the voice chat button") {
        let f = fixture(draft: "my draft", sendEnabled: false)
        f.send.attributes["AXDescription"] = "Start talechat"
        try expectFailure("No Send button") { _ = try run(f) }
        try expectNoReturn(f, typed: 0)
    }
    test("refuses oversized trees") {
        let f = fixture()
        f.window.add((0...maxNodes).map { _ in FakeNode("AXGroup") })
        try expectFailure("more than \(maxNodes)") { _ = try findTarget(in: f.window) }
    }
    test("refuses a missing focused window") {
        let f = fixture()
        f.app.attributes["AXFocusedWindow"] = nil
        try expectFailure("no focused window") { _ = try run(f) }
        try expect(f.host.activateCalls == 0, "activated the app")
    }
    test("refuses an unreadable composer") {
        let f = fixture(draft: nil)
        try expectFailure("unreadable") { _ = try run(f) }
        try expectNoReturn(f)
    }
    test("reads the exact placeholder as empty and anything else as a draft") {
        let f = fixture()
        let cases: [(String, String)] = [
            ("Spør ChatGPT", ""), ("Spør ChatGPT\n", ""), (" Spør ChatGPT", ""), ("test", "test"),
            ("spør chatgpt", "spør chatgpt"), ("Spør ChatGPT!", "Spør ChatGPT!"), ("Spør ChatGPT om", "Spør ChatGPT om"),
            ("Spør  ChatGPT", "Spør  ChatGPT"), (" \n", " \n"),
        ]
        for (value, expected) in cases {
            f.composer.attributes["AXValue"] = value
            let text = try composerText(try findTarget(in: f.window))
            try expect(text == expected, "read \(value.debugDescription) as \(text.debugDescription)")
        }
    }
    test("types continue into a placeholder-only composer after focusing, then presses Return") {
        // The live app reports an empty editor as its placeholder plus a newline.
        let f = fixture(draft: "Spør ChatGPT\n", sendEnabled: false)
        f.host.onReturn = { f.composer.attributes["AXValue"] = "Spør ChatGPT\n" }
        let result = try run(f)
        try expect(result.inserted && result.outcome == .confirmed, "unexpected result \(result)")
        try expect(f.host.activateCalls == 1 && f.composer.sets == ["AXFocused=true"], "did not activate and focus once")
        try expect(f.host.typed.count == 1, "typed \(f.host.typed.count) times")
        let typing = f.host.typed[0]
        try expect(typing.frontmost && typing.focused && typing.before == "Spør ChatGPT\n" && typing.text == continueText,
                   "typed into an unverified composer")
        try expect(f.host.returns.count == 1 && f.host.returns[0].text == continueText, "Return not sent with typed text")
        try expect(f.composer.valueSets.isEmpty, "set AXValue")
    }
    test("types after whitespace-only text and sends what the editor holds") {
        let f = fixture(draft: " \n", sendEnabled: false)
        let result = try run(f)
        try expect(result.inserted && f.host.typed.count == 1, "unexpected result \(result)")
        try expect(f.host.returns.count == 1 && f.host.returns[0].text == " \ncontinue", "Return not sent once")
    }
    test("keeps a short draft such as test") {
        let f = fixture(draft: "test")
        let result = try run(f)
        try expect(!result.inserted && result.outcome == .confirmed, "unexpected result \(result)")
        try expect(f.host.typed.isEmpty && f.composer.valueSets.isEmpty, "edited the draft")
        try expect(f.host.returns.count == 1 && f.host.returns[0].text == "test", "draft not sent once")
    }
    test("keeps a non-empty draft, focuses the composer and presses Return once") {
        let f = fixture(draft: "please continue the refactor")
        let result = try run(f)
        try expect(!result.inserted && result.outcome == .confirmed, "unexpected result \(result)")
        try expect(f.host.typed.isEmpty && f.composer.valueSets.isEmpty, "edited the draft")
        try expect(f.host.activateCalls == 1 && f.composer.sets == ["AXFocused=true"], "did not activate and focus once")
        try expect(f.host.returns.count == 1, "pressed Return \(f.host.returns.count) times")
        let press = f.host.returns[0]
        try expect(press.frontmost && press.focused && press.text == "please continue the refactor", "Return not sent to the verified composer")
    }
    test("waits for Send disabled briefly by activation, then presses Return once") {
        let f = fixture(draft: "keep going")
        f.host.onActivate = { f.send.attributes["AXEnabled"] = false }
        var sleeps = 0
        let result = try run(f, sleep: { _ in
            sleeps += 1
            if sleeps == 3 { f.send.attributes["AXEnabled"] = true }
        })
        try expect(result.outcome == .confirmed, "unexpected result \(result)")
        try expect(f.host.returns.count == 1 && f.host.returns[0].text == "keep going", "draft not sent once")
    }
    test("skips Return when Send stays disabled after activation") {
        let f = fixture(draft: "keep going")
        f.host.onActivate = { f.send.attributes["AXEnabled"] = false }
        try expectFailure("Send became disabled or unavailable") { _ = try run(f) }
        try expectNoReturn(f)
        try expect(f.composer.attributes["AXValue"] as? String == "keep going", "draft changed")
    }
    test("leaves typed continue in place and skips Return when Send stays disabled") {
        let f = fixture(draft: "", sendEnabled: false)
        f.host.typingEnablesSend = false
        var sleeps = 0
        try expectFailure("Send stayed disabled; left it in the composer") { _ = try run(f, sleep: { _ in sleeps += 1 }) }
        try expect(sleeps == enableAttempts, "polled \(sleeps) times after typing")
        try expectNoReturn(f, typed: 1)
        try expect(f.composer.attributes["AXValue"] as? String == continueText, "typed text was changed")
    }
    test("skips Return when typed text does not appear") {
        let f = fixture(draft: "", sendEnabled: false)
        f.host.typingAccepted = false
        try expectFailure("did not fully appear") { _ = try run(f) }
        try expectNoReturn(f, typed: 1)
        try expect(f.composer.attributes["AXValue"] as? String == "", "composer changed")
    }
    test("skips Return when unexpected text appears after typing") {
        let f = fixture(draft: "", sendEnabled: false)
        f.host.onType = { f.composer.attributes["AXValue"] = "my own words" }
        try expectFailure("unexpected text") { _ = try run(f) }
        try expectNoReturn(f, typed: 1)
        try expect(f.composer.attributes["AXValue"] as? String == "my own words", "user text was changed")
    }
    test("does not type when a draft appears after activation") {
        let f = fixture(draft: "", sendEnabled: false)
        f.composer.onSet = { name, _ in if name == "AXFocused" { f.composer.attributes["AXValue"] = "hello" } }
        try expectFailure("text changed") { _ = try run(f) }
        try expectNoReturn(f)
        try expect(f.composer.attributes["AXValue"] as? String == "hello", "user text was changed")
    }
    test("does not type when another app takes the front after focusing") {
        let f = fixture(draft: "", sendEnabled: false)
        f.composer.onSet = { name, _ in if name == "AXFocused" { f.host.frontmost = false } }
        try expectFailure("Another app came to the front") { _ = try run(f) }
        try expectNoReturn(f)
    }
    test("does not type when the window changes after focusing") {
        let f = fixture(draft: "", sendEnabled: false)
        let other = fixture()
        f.composer.onSet = { name, _ in if name == "AXFocused" { f.app.attributes["AXFocusedWindow"] = other.window } }
        try expectFailure("focused window changed") { _ = try run(f) }
        try expectNoReturn(f)
    }
    test("rechecks the chosen chat before typing and before Return") {
        func chosen(_ f: Fixture) throws -> (inserted: Bool, outcome: Outcome) {
            try submit(app: f.app, foreground: f.host.foreground(f), requiredIdle: scheduledIdleSeconds,
                       chatTitle: "Target", sleep: { f.host.clock += $0 })
        }
        let kept = fixture(draft: "", sendEnabled: false)
        kept.window.attributes["AXTitle"] = "Target"
        kept.host.userActivity(secondsAgo: 120)
        try expect(try chosen(kept).outcome == .confirmed, "did not submit to the chosen chat")

        let early = fixture(draft: "", sendEnabled: false)
        early.window.attributes["AXTitle"] = "Target"
        early.host.userActivity(secondsAgo: 120)
        early.host.onActivate = { early.window.attributes["AXTitle"] = "Other chat" }
        let before = try failure("instead of chat \"Target\"") { _ = try chosen(early) }
        try expect(before.retryable && early.host.typed.isEmpty, "typed into another chat or not retryable")
        try expectNoReturn(early)

        let late = fixture(draft: "", sendEnabled: false)
        late.window.attributes["AXTitle"] = "Target"
        late.host.userActivity(secondsAgo: 120)
        late.host.onType = { late.window.attributes["AXTitle"] = "Other chat" }
        let after = try failure("instead of chat \"Target\"") { _ = try chosen(late) }
        try expect(!after.retryable && late.host.typed.count == 1 && late.host.returns.isEmpty,
                   "pressed Return in another chat or retryable after typing")
    }
    test("refuses a disabled composer before any input, retryably") {
        let f = fixture(draft: nil)
        f.composer.attributes["AXEnabled"] = false
        let error = try failure("composer is disabled") { _ = try runScheduled(f) }
        try expect(error.retryable && f.host.activateCalls == 0 && f.composer.sets.isEmpty, "not a clean retryable stop")
        try expectNoReturn(f)
    }
    test("refuses a disabled Send with a non-empty draft without activating") {
        let f = fixture(draft: "draft", sendEnabled: false)
        var sleeps = 0
        try expectFailure("stayed disabled") { _ = try run(f, sleep: { _ in sleeps += 1 }) }
        try expect(sleeps == enableAttempts && f.host.activateCalls == 0, "polled \(sleeps), activated \(f.host.activateCalls)")
        try expect(f.composer.sets.isEmpty, "touched the composer")
        try expectNoReturn(f)
    }
    test("stops when the focused window changes while waiting for Send") {
        let f = fixture(draft: "draft", sendEnabled: false)
        let other = fixture()
        try expectFailure("focused window changed") {
            _ = try run(f, sleep: { _ in f.app.attributes["AXFocusedWindow"] = other.window })
        }
        try expectNoReturn(f)
    }
    test("stops when the draft changes while waiting for Send") {
        let f = fixture(draft: "draft", sendEnabled: false)
        try expectFailure("text changed") {
            _ = try run(f, sleep: { _ in
                f.composer.attributes["AXValue"] = "edited"
                f.send.attributes["AXEnabled"] = true
            })
        }
        try expectNoReturn(f)
    }
    test("refuses while a reply is in progress") {
        let f = fixture()
        f.send.attributes["AXDescription"] = "Stop"
        try expectFailure("in progress") { _ = try run(f) }
        try expectNoReturn(f)
    }
    test("refuses a composer that cannot be focused, before activating") {
        let f = fixture(draft: "")
        f.composer.settable = []
        try expectFailure("cannot be focused") { _ = try run(f) }
        try expect(f.host.activateCalls == 0, "activated the app")
        try expectNoReturn(f)
    }
    test("refuses when the app does not come to the front") {
        let f = fixture(draft: "")
        f.host.activates = false
        var sleeps = 0
        try expectFailure("did not come to the front") { _ = try run(f, sleep: { _ in sleeps += 1 }) }
        try expect(f.host.activateCalls == 1 && sleeps == foregroundAttempts, "activated \(f.host.activateCalls), polled \(sleeps)")
        try expect(f.composer.sets.isEmpty, "focused before the app was frontmost")
        try expectNoReturn(f)
    }
    test("refuses before activating while secure keyboard entry is active") {
        let f = fixture(draft: "")
        f.host.secureInputOwner = "Terminal (pid 42)"
        try expectFailure("Secure keyboard entry is active in Terminal (pid 42)") { _ = try run(f) }
        try expect(f.host.activateCalls == 0 && f.host.typed.isEmpty, "activated or typed")
        try expect(f.composer.sets.isEmpty, "focused the composer")
        try expectNoReturn(f)
    }
    test("names secure keyboard entry when activation is refused") {
        let f = fixture(draft: "")
        f.host.activates = false
        f.host.secureInputAfterActivation = "an unidentified app"
        try expectFailure("Secure keyboard entry is active in an unidentified app") { _ = try run(f, sleep: { _ in }) }
        try expect(f.host.activateCalls == 1 && f.host.typed.isEmpty && f.composer.sets.isEmpty, "typed or focused")
        try expectNoReturn(f)
    }
    test("refuses when the frontmost app cannot be read") {
        let f = fixture(draft: "")
        f.host.frontmostReadable = false
        try expectFailure("frontmost app unreadable") { _ = try run(f) }
        try expect(f.composer.sets.isEmpty, "focused without a frontmost check")
        try expectNoReturn(f)
    }
    test("refuses when the frontmost app becomes unreadable just before Return") {
        let f = fixture()
        f.composer.onSet = { name, _ in if name == "AXFocused" { f.host.frontmostReadable = false } }
        try expectFailure("frontmost app unreadable") { _ = try run(f) }
        try expectNoReturn(f)
    }
    test("refuses when composer focus cannot be confirmed") {
        let f = fixture(draft: "")
        f.composer.ignoredSets = ["AXFocused"]
        try expectFailure("Could not confirm focus") { _ = try run(f) }
        try expectNoReturn(f)
    }
    test("refuses when another app takes the front just before Return") {
        let f = fixture()
        f.composer.onSet = { name, _ in if name == "AXFocused" { f.host.frontmost = false } }
        try expectFailure("Another app came to the front") { _ = try run(f) }
        try expectNoReturn(f)
    }
    test("confirms submission when Stop appears") {
        let f = fixture()
        f.host.onReturn = { f.send.attributes["AXDescription"] = "Stop" }
        let result = try run(f)
        try expect(result.outcome == .confirmed && f.host.returns.count == 1, "unexpected result \(result)")
    }
    test("reports an unconfirmed Return") {
        let f = fixture()
        f.host.onReturn = nil
        let result = try run(f)
        try expect(f.host.returns.count == 1 && result.outcome == .unconfirmed, "unexpected result \(result)")
    }

    test("scheduled: 59 s without input blocks before activating, retryably") {
        let f = fixture(draft: "", sendEnabled: false)
        f.host.userActivity(secondsAgo: 59)
        let error = try failure("activity 59 s ago") { _ = try runScheduled(f) }
        try expect(error.retryable && f.host.activateCalls == 0 && f.composer.sets.isEmpty, "not a clean retryable stop")
        try expectNoReturn(f)
    }
    test("scheduled: 60 s without input types and presses Return once") {
        let f = fixture(draft: "", sendEnabled: false)
        f.host.userActivity(secondsAgo: 60)
        let result = try runScheduled(f)
        try expect(result.inserted && result.outcome == .confirmed, "unexpected result \(result)")
        try expect(f.host.typed.count == 1 && f.host.returns.count == 1, "typed \(f.host.typed.count), returns \(f.host.returns.count)")
    }
    test("scheduled: own typing does not count as user activity") {
        let f = fixture(draft: "", sendEnabled: false)
        f.host.typingEnablesSend = false
        let result = try runScheduled(f, sleep: { t in
            f.host.clock += t
            if !f.host.typed.isEmpty { f.send.attributes["AXEnabled"] = true }
        })
        try expect(result.outcome == .confirmed && f.host.returns.count == 1, "unexpected result \(result)")
    }
    test("scheduled: activity after activation stops before typing, retryably") {
        let f = fixture(draft: "", sendEnabled: false)
        f.composer.onSet = { name, _ in if name == "AXFocused" { f.host.userActivity() } }
        let error = try failure("activity 0 s ago") { _ = try runScheduled(f) }
        try expect(error.retryable, "not retryable")
        try expectNoReturn(f)
    }
    test("scheduled: activity before Return on a draft stops, retryably") {
        let f = fixture()
        f.composer.onSet = { name, _ in if name == "AXFocused" { f.host.userActivity() } }
        let error = try failure("activity 0 s ago") { _ = try runScheduled(f) }
        try expect(error.retryable && f.composer.attributes["AXValue"] as? String == "keep going", "not retryable or draft changed")
        try expectNoReturn(f)
    }
    test("scheduled: activity after typing stops before Return, permanently") {
        let f = fixture(draft: "", sendEnabled: false)
        f.host.typingEnablesSend = false
        let error = try failure("activity resumed after typing") {
            _ = try runScheduled(f, sleep: { t in
                f.host.clock += t * 10
                if !f.host.typed.isEmpty { f.send.attributes["AXEnabled"] = true; f.host.userActivity() }
            })
        }
        try expect(!error.retryable, "retryable after typing")
        try expectNoReturn(f, typed: 1)
    }
    test("scheduled: typed text with Send disabled is permanent") {
        let f = fixture(draft: "", sendEnabled: false)
        f.host.typingEnablesSend = false
        let error = try failure("Send stayed disabled; left it") { _ = try runScheduled(f) }
        try expect(!error.retryable, "retryable after typing")
        try expectNoReturn(f, typed: 1)
    }
    test("scheduled: a transient error from typing becomes permanent") {
        let f = fixture(draft: "", sendEnabled: false)
        f.host.typeError = Failure("fake: typing interrupted", retryable: true)
        let error = try failure("typing interrupted") { _ = try runScheduled(f) }
        try expect(!error.retryable, "retryable after typing")
        try expectNoReturn(f, typed: 1)
    }
    test("scheduled: an error after Return is permanent") {
        let f = fixture()
        f.host.returnError = Failure("fake: Return timed out", retryable: true)
        let error = try failure("Return timed out") { _ = try runScheduled(f) }
        try expect(!error.retryable && f.host.returns.count == 1, "retryable after Return")
    }
    test("scheduled: an unconfirmed Return is reported, not retried") {
        let f = fixture()
        f.host.onReturn = nil
        let result = try runScheduled(f)
        try expect(result.outcome == .unconfirmed && f.host.returns.count == 1, "unexpected result \(result)")
    }
    test("scheduled: unreadable idle time stops permanently before activating") {
        let f = fixture(draft: "")
        f.host.idleReadable = false
        let error = try failure("idle unavailable") { _ = try runScheduled(f) }
        try expect(!error.retryable && f.host.activateCalls == 0, "retryable or activated")
        try expectNoReturn(f)
    }
    test("immediate submit ignores the idle clock") {
        let f = fixture()
        f.host.userActivity()
        try expect(try run(f).outcome == .confirmed, "did not submit")
    }
    test("initial scheduled attempt ignores activity for drafts, empty and unknown composers") {
        let mode = SubmissionMode.scheduledInitial
        try expect(mode.scheduled && mode.requiredIdle == nil, "initial attempt uses the retry idle gate")
        for kind in ["draft", "empty", "unknown"] {
            let f = fixture(draft: kind == "draft" ? "my draft" : "")
            if kind == "unknown" { f.composer.attributes["AXDescription"] = "Other" }
            f.host.userActivity()
            f.host.idleReadable = false
            let result = try submit(app: f.app, foreground: f.host.foreground(f), requiredIdle: mode.requiredIdle, sleep: { _ in })
            try expect(f.host.returns.count == 1 && f.host.returns[0].frontmost, "initial attempt skipped Return")
            try expect(f.host.typed.count == (kind == "empty" ? 1 : 0), "incorrect draft handling")
            try expect(result.outcome == (kind == "unknown" ? .unverifiedReturn : .confirmed), "wrong outcome")
        }
    }
    test("retry mode still requires inactivity after an initial pre-input failure") {
        let mode = SubmissionMode.scheduledRetry
        try expect(mode.scheduled && mode.requiredIdle == 60, "retry mode does not require 60 s")
        let f = fixture()
        f.host.userActivity(secondsAgo: 59)
        let error = try failure("activity 59 s ago") {
            _ = try submit(app: f.app, foreground: f.host.foreground(f), requiredIdle: mode.requiredIdle, sleep: { _ in })
        }
        try expect(exitCode(for: error, scheduled: mode.scheduled) == 4, "retryable activity was permanent")
        try expectNoReturn(f)
        try expect(f.host.activateCalls == 0, "retry activated while user was active")
    }
    test("initial scheduled attempt retains retryable pre-input guards") {
        let f = fixture()
        f.host.secureInputOwner = "Terminal"
        f.host.userActivity()
        let mode = SubmissionMode.scheduledInitial
        let error = try failure("Secure keyboard entry") {
            _ = try submit(app: f.app, foreground: f.host.foreground(f), requiredIdle: mode.requiredIdle, sleep: { _ in })
        }
        try expect(exitCode(for: error, scheduled: mode.scheduled) == 4, "initial safe failure not retryable")
        try expect(exitCode(for: error, scheduled: SubmissionMode.immediate.scheduled) == 1, "immediate became retryable")
        try expectNoReturn(f)
    }
    test("unrecognised composer sends one foreground Return without typing or focusing") {
        for scheduled in [true, false] {
            let f = fixture(draft: "")
            f.composer.attributes["AXDescription"] = "Other"
            let result = scheduled ? try runScheduled(f) : try run(f)
            try expect(!result.inserted && result.outcome == .unverifiedReturn, "claimed a verified submission")
            try expect(f.host.activateCalls == 1 && f.host.returns.count == 1, "did not activate and press Return once")
            try expect(f.host.returns[0].frontmost, "pressed Return in another app")
            try expect(f.host.typed.isEmpty && f.composer.valueSets.isEmpty && f.composer.sets.isEmpty, "typed or changed focus")
        }
    }
    test("fallback preserves an unknown draft and never confirms it even when it clears") {
        let f = fixture(draft: "my prepared instruction")
        f.composer.attributes["AXDescription"] = "Other"
        f.composer.attributes["AXFocused"] = true
        let result = try runScheduled(f)
        try expect(f.host.returns.count == 1 && f.host.returns[0].text == "my prepared instruction", "draft not preserved")
        try expect(f.host.typed.isEmpty && result.outcome == .unverifiedReturn, "typed or claimed confirmation")
    }
    test("fallback works without any editable controls or readable draft") {
        let f = fixture(draft: nil)
        f.window.kids = []
        let result = try run(f)
        try expect(result.outcome == .unverifiedReturn && f.host.returns.count == 1, "did not press Return once")
        try expect(f.host.typed.isEmpty && f.composer.sets.isEmpty, "typed or changed focus")
    }
    test("fallback waits for inactivity and respects Secure Input and activation failures") {
        let active = fixture()
        active.host.userActivity(secondsAgo: 59)
        let secure = fixture()
        secure.host.secureInputOwner = "Terminal"
        let background = fixture()
        background.host.activates = false
        for (f, fragment) in [(active, "activity 59 s ago"), (secure, "Secure keyboard entry"), (background, "did not come to the front")] {
            f.composer.attributes["AXDescription"] = "Other"
            let error = try failure(fragment) { _ = try runScheduled(f) }
            try expect(exitCode(for: error, scheduled: true) == 4, "pre-input failure not retryable")
            try expectNoReturn(f)
        }
        try expect(active.host.activateCalls == 0 && secure.host.activateCalls == 0, "activated while blocked")
    }
    test("fallback refuses an unreadable frontmost app") {
        let f = fixture()
        f.composer.attributes["AXDescription"] = "Other"
        f.host.frontmostReadable = false
        let error = try failure("frontmost app unreadable") { _ = try runScheduled(f) }
        try expect(!error.retryable, "unknown foreground was retryable")
        try expectNoReturn(f)
    }
    test("fallback rechecks inactivity, Secure Input, window and frontmost app after activation") {
        for change in ["activity", "secure", "window", "frontmost"] {
            let f = fixture()
            f.composer.attributes["AXDescription"] = "Other"
            var fragment = ""
            switch change {
            case "activity":
                fragment = "activity 0 s ago"
                f.host.onActivate = { f.host.userActivity() }
            case "secure":
                fragment = "Secure keyboard entry"
                f.host.secureInputAfterActivation = "Terminal"
            case "window":
                fragment = "focused window changed"
                f.host.onActivate = { f.app.attributes["AXFocusedWindow"] = FakeNode("AXWindow") }
            default:
                fragment = "did not come to the front"
                f.host.onActivate = { f.host.frontmost = false }
            }
            let error = try failure(fragment) { _ = try runScheduled(f) }
            try expect(exitCode(for: error, scheduled: true) == 4, "pre-input failure not retryable")
            try expectNoReturn(f)
        }
    }
    test("fallback Return errors cannot allow a scheduled retry") {
        let f = fixture()
        f.composer.attributes["AXDescription"] = "Other"
        f.host.returnError = Failure("fake: Return timed out", retryable: true)
        let error = try failure("Return timed out") { _ = try runScheduled(f) }
        try expect(exitCode(for: error, scheduled: true) == 1 && f.host.returns.count == 1, "retryable after Return")
        try expect(f.host.typed.isEmpty, "typed into an unknown composer")
    }
    test("missing window is retryable; ambiguity and Accessibility errors are not") {
        let noWindow = fixture()
        noWindow.app.attributes["AXFocusedWindow"] = nil
        try expect(try failure("no focused window") { _ = try runScheduled(noWindow) }.retryable, "missing window not retryable")
        let twoFocused = fixture()
        twoFocused.composer.attributes["AXFocused"] = true
        twoFocused.window.add([FakeNode("AXTextArea", ["AXDescription": "Ask ChatGPT", "AXFocused": true])])
        try expect(!(try failure("cannot tell which") { _ = try runScheduled(twoFocused) }.retryable), "ambiguous composer retryable")
        let twoSends = fixture()
        twoSends.toolbar.add([button("Send")])
        try expect(!(try failure("several Send") { _ = try runScheduled(twoSends) }.retryable), "ambiguous Send retryable")
        let unfocusable = fixture()
        unfocusable.composer.settable = []
        try expect(!(try failure("cannot be focused") { _ = try runScheduled(unfocusable) }.retryable), "unfocusable retryable")
        for f in [noWindow, twoFocused, twoSends, unfocusable] { try expectNoReturn(f) }
    }
    test("pre-injection transient states are retryable") {
        let secure = fixture(draft: "")
        secure.host.secureInputOwner = "Terminal (pid 42)"
        try expect(try failure("Secure keyboard entry") { _ = try runScheduled(secure) }.retryable, "secure input not retryable")
        let background = fixture(draft: "")
        background.host.activates = false
        try expect(try failure("did not come to the front") { _ = try runScheduled(background) }.retryable, "activation not retryable")
        let disabled = fixture(sendEnabled: false)
        try expect(try failure("stayed disabled or unavailable") { _ = try runScheduled(disabled) }.retryable, "disabled Send not retryable")
        let busy = fixture()
        busy.send.attributes["AXDescription"] = "Stop"
        try expect(try failure("in progress") { _ = try runScheduled(busy) }.retryable, "reply in progress not retryable")
        for f in [secure, background, disabled, busy] { try expectNoReturn(f) }
    }
    test("in-progress label with Stopp: scheduled retries, immediate refuses, no input") {
        for scheduled in [true, false] {
            let f = fixture()
            f.composer.attributes["AXDescription"] = "Arbeid med ChatGPT"
            f.send.attributes["AXDescription"] = "Stopp"
            let target = try findTarget(in: f.window)
            try expect(target.composer === f.composer && target.stop === f.send && target.send == nil, "wrong target")
            let error = try failure("reply appears to be in progress") { _ = scheduled ? try runScheduled(f) : try run(f) }
            try expect(exitCode(for: error, scheduled: scheduled) == (scheduled ? 4 : 1), "exit code")
            try expect(f.host.activateCalls == 0 && f.composer.sets.isEmpty, "activated or focused")
            try expectNoReturn(f)
        }
    }
    test("custom message: typed into an empty composer and sent once") {
        let f = fixture(draft: "Spør ChatGPT\n", sendEnabled: false)
        f.host.onReturn = { f.composer.attributes["AXValue"] = "Spør ChatGPT\n" }
        let message = "If the usage limit stopped you, continue; otherwise do nothing."
        let result = try submit(app: f.app, foreground: f.host.foreground(f), message: message, sleep: { _ in })
        try expect(result.inserted && result.outcome == .confirmed, "unexpected result \(result)")
        try expect(f.host.typed.count == 1 && f.host.typed[0].text == message, "did not type the message once")
        try expect(f.host.returns.count == 1 && f.host.returns[0].text == message, "Return not sent with the message")
    }
    test("custom message: added after a draft with a blank line and sent together once") {
        let f = fixture(draft: "my draft\nsecond line")
        let result = try submit(app: f.app, foreground: f.host.foreground(f), message: "resume", sleep: { _ in })
        try expect(!result.inserted && result.outcome == .confirmed, "unexpected result \(result)")
        try expect(f.host.breaks.count == 1 && f.host.breaks[0].frontmost && f.host.breaks[0].focused,
                   "blank line not added once in the focused composer")
        try expect(f.host.typed.count == 1 && f.host.typed[0].before == "my draft\nsecond line\n\n", "message not typed after the break")
        try expect(f.host.returns.count == 1 && f.host.returns[0].text == "my draft\nsecond line\n\nresume",
                   "Return not sent with draft and message")
    }
    test("custom message: no Return when the text after a draft is not draft plus message") {
        let f = fixture(draft: "my draft")
        f.host.onType = { f.composer.attributes["AXValue"] = "myresume draft" }
        let error = try failure("unexpected text") {
            _ = try submit(app: f.app, foreground: f.host.foreground(f), message: "resume", sleep: { _ in })
        }
        try expect(!error.retryable, "retryable after typing")
        try expectNoReturn(f, typed: 1)
    }
    test("without a custom message a draft gets no blank line or text") {
        let f = fixture(draft: "my draft")
        let result = try run(f)
        try expect(!result.inserted && result.outcome == .confirmed && f.host.breaks.isEmpty && f.host.typed.isEmpty,
                   "changed the draft")
    }
    test("custom message: an unrecognised composer never gets the Return-only fallback") {
        let f = fixture(draft: "")
        f.composer.attributes["AXDescription"] = "Other"
        let error = try failure("needs a recognised composer") {
            _ = try submit(app: f.app, foreground: f.host.foreground(f), requiredIdle: scheduledIdleSeconds,
                           message: "resume", sleep: { _ in })
        }
        try expect(exitCode(for: error, scheduled: true) == 4, "not retryable")
        try expect(f.host.activateCalls == 0, "activated the app")
        try expectNoReturn(f)
    }
    test("custom message: partial or altered text is never sent") {
        let f = fixture(draft: "", sendEnabled: false)
        f.host.onType = { f.composer.attributes["AXValue"] = "resume “now”" }
        let error = try failure("unexpected text") {
            _ = try submit(app: f.app, foreground: f.host.foreground(f), message: "resume \"now\"", sleep: { _ in })
        }
        try expect(!error.retryable, "retryable after typing")
        try expectNoReturn(f, typed: 1)
    }
    test("custom message validation trims and rejects multi-line, control and slash messages") {
        try expect(try validatedMessage("  keep going \n") == "keep going", "did not trim")
        for bad in ["", "  \n", "line one\nline two", "tab\there", "/compact"] {
            try expectFailure("message") { _ = try validatedMessage(bad) }
        }
    }
    test("exit 4 only for retryable failures in scheduled mode") {
        let transient = Failure("x", retryable: true)
        try expect(exitCode(for: transient, scheduled: true) == 4, "scheduled transient")
        try expect(exitCode(for: transient, scheduled: false) == 1, "immediate transient")
        try expect(exitCode(for: Failure("x"), scheduled: true) == 1, "scheduled permanent")
        try expect(exitCode(for: CocoaError(.fileNoSuchFile), scheduled: true) == 1, "foreign error")
    }

    for failure in failures { FileHandle.standardError.write(Data("FAIL \(failure)\n".utf8)) }
    print("\(count - failures.count)/\(count) self-tests passed")
    return failures.isEmpty ? 0 : 1
}

// MARK: - Entry point

func usageError(_ detail: String? = nil) -> Never {
    if let detail { FileHandle.standardError.write(Data("\(detail)\n".utf8)) }
    let chatOptions = "[--chat ID --chat-title TITLE [--chat-host HOST]]"
    FileHandle.standardError.write(Data(("usage: codex-resume.swift --self-test | (--check | --inspect) \(chatOptions) | "
        + "(--submit | --scheduled-initial-submit | --scheduled-submit | --scheduled-final-submit) "
        + "[--message TEXT] \(chatOptions)\n").utf8))
    exit(2)
}

let arguments = Array(CommandLine.arguments.dropFirst())
guard let action = arguments.first, arguments.count % 2 == 1 else { usageError() }
let submissionMode = SubmissionMode(rawValue: action)
var options: [String: String] = [:]
for index in stride(from: 1, to: arguments.count, by: 2) {
    let name = arguments[index]
    let allowed = ["--chat", "--chat-title", "--chat-host"] + (submissionMode != nil ? ["--message"] : [])
    guard action != "--self-test", allowed.contains(name), options[name] == nil else { usageError() }
    options[name] = arguments[index + 1]
}
guard (options["--chat"] == nil) == (options["--chat-title"] == nil) else { usageError("--chat and --chat-title go together.") }
var message: String?
if let text = options["--message"] {
    do { message = try validatedMessage(text) } catch { usageError("\(error)") }
}
guard options["--chat-host"] == nil || options["--chat"] != nil else { usageError("--chat-host needs --chat.") }
let chat = options["--chat"].map { Chat(id: $0, title: options["--chat-title"] ?? "", host: options["--chat-host"]) }
do {
    if let mode = submissionMode {
        exit(try submitLive(mode: mode, message: message, chat: chat))
    }
    switch action {
    case "--check": try check(chat: chat)
    case "--inspect": try inspect(chat: chat)
    case "--self-test": exit(selfTest())
    default:
        FileHandle.standardError.write(Data("unknown option: \(arguments[0])\n".utf8))
        exit(2)
    }
} catch {
    FileHandle.standardError.write(Data("\(error)\n".utf8))
    exit(exitCode(for: error, scheduled: submissionMode?.scheduled == true))
}
