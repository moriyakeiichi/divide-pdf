import AppKit
import UniformTypeIdentifiers

final class FlippedStackView: NSStackView {
    override var isFlipped: Bool { true }
}

final class DropView: NSView {
    var receive: (([URL]) -> Void)?
    var busy = false
    private var hovering = false
    override init(frame: NSRect) {
        super.init(frame: frame)
        registerForDraggedTypes([.fileURL])
        wantsLayer = true
        layer?.cornerRadius = 14
        layer?.borderWidth = 1.5
        updateColor()
    }
    required init?(coder: NSCoder) { fatalError() }
    override func viewDidChangeEffectiveAppearance() { super.viewDidChangeEffectiveAppearance(); updateColor() }
    func updateColor() {
        effectiveAppearance.performAsCurrentDrawingAppearance {
            layer?.backgroundColor = NSColor.controlAccentColor.withAlphaComponent(hovering ? 0.16 : 0.055).cgColor
            layer?.borderColor = NSColor.controlAccentColor.withAlphaComponent(hovering ? 0.8 : 0.3).cgColor
        }
    }
    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation {
        guard !busy, sender.draggingPasteboard.canReadObject(forClasses: [NSURL.self], options: [.urlReadingFileURLsOnly: true]) else { return [] }
        hovering = true; updateColor(); return .copy
    }
    override func draggingExited(_ sender: NSDraggingInfo?) { hovering = false; updateColor() }
    override func prepareForDragOperation(_ sender: NSDraggingInfo) -> Bool { !busy }
    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        hovering = false; updateColor()
        guard !busy, let urls = sender.draggingPasteboard.readObjects(forClasses: [NSURL.self], options: [.urlReadingFileURLsOnly: true]) as? [URL] else { return false }
        receive?(urls); return true
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, NSTableViewDataSource, NSTableViewDelegate, NSTextFieldDelegate {
    let operation = Bundle.main.object(forInfoDictionaryKey: "PDFOperation") as? String ?? "split"
    var isSplit: Bool { operation == "split" }
    var appName: String { isSplit ? "見開きPDF分割" : "注釈ページ抽出" }
    let defaults = UserDefaults.standard
    var window: NSWindow!
    let drop = DropView()
    let table = NSTableView()
    let log = NSTextView()
    let status = NSTextField(labelWithString: "PDFまたはフォルダを追加してください。")
    let progress = NSProgressIndicator()
    let countLabel = NSTextField(labelWithString: "入力 0件")
    var inputs: [URL] = []
    var outputs: [URL] = []
    var process: Process?
    var isRunning = false
    var cancelled = false
    var fatalMessage: String?
    var counts = ["ok": 0, "skip": 0, "error": 0]
    var selectedOutput: URL?
    var logText = ""
    var deferredURLs: [URL] = []
    var controls: [NSControl] = []
    let startButton = NSButton(title: "処理を開始", target: nil, action: nil)
    let cancelButton = NSButton(title: "中止", target: nil, action: nil)
    let revealButton = NSButton(title: "出力をFinderで表示", target: nil, action: nil)
    let outputMode = NSPopUpButton()
    let outputPath = NSTextField(labelWithString: "未指定")
    let chooseOutput = NSButton(title: "保存先を選ぶ…", target: nil, action: nil)
    let suffix = NSTextField()
    let existing = NSPopUpButton()
    let recursive = NSButton(checkboxWithTitle: "下位フォルダのPDFも処理する", target: nil, action: nil)
    let pages = NSTextField()
    let order = NSPopUpButton()
    let scope = NSPopUpButton()
    let ratio = NSTextField()
    let mode = NSPopUpButton()
    let dpi = NSTextField()
    let spanning = NSPopUpButton()
    let filter = NSPopUpButton()
    let context = NSPopUpButton()
    let password = NSSecureTextField()
    let autoRun = NSButton(checkboxWithTitle: "ドロップしたらすぐに処理する", target: nil, action: nil)
    let showFinder = NSButton(checkboxWithTitle: "完了後に出力をFinderで表示する", target: nil, action: nil)
    var settingsStack: NSStackView!
    var infoLabel: NSTextField!

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        buildWindow()
        loadSettings()
        refreshEnabled()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        if !deferredURLs.isEmpty { let urls = deferredURLs; deferredURLs = []; add(urls, fromDrop: true) }
    }
    func application(_ application: NSApplication, open urls: [URL]) {
        if window == nil { deferredURLs.append(contentsOf: urls) }
        else { window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true); add(urls, fromDrop: true) }
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        window.makeKeyAndOrderFront(nil); return true
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if isRunning {
            let alert = NSAlert()
            alert.messageText = "処理を中止して終了しますか？"
            alert.informativeText = "保存済みのPDFは残ります。作成途中のPDFは公開しません。"
            alert.addButton(withTitle: "処理を続ける")
            alert.addButton(withTitle: "中止して終了")
            if alert.runModal() == .alertFirstButtonReturn { return .terminateCancel }
            process?.terminate()
        }
        saveSettings()
        return .terminateNow
    }
    func windowShouldClose(_ sender: NSWindow) -> Bool {
        if isRunning { NSApp.terminate(nil); return false }
        saveSettings(); return true
    }

    func buildMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem(); main.addItem(appItem)
        let appMenu = NSMenu(); appItem.submenu = appMenu
        appMenu.addItem(withTitle: "\(appName)について", action: #selector(about), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "設定に移動…", action: #selector(focusSettings), keyEquivalent: ",")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "\(appName)を終了", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let fileItem = NSMenuItem(); main.addItem(fileItem)
        let fileMenu = NSMenu(title: "ファイル"); fileItem.submenu = fileMenu
        fileMenu.addItem(withTitle: "PDFを追加…", action: #selector(addFiles), keyEquivalent: "o")
        let folderItem = fileMenu.addItem(withTitle: "フォルダを追加…", action: #selector(addFolder), keyEquivalent: "o"); folderItem.keyEquivalentModifierMask = [.command, .shift]
        fileMenu.addItem(withTitle: "結果を保存…", action: #selector(saveLog), keyEquivalent: "s")
        let editItem = NSMenuItem(); main.addItem(editItem)
        let editMenu = NSMenu(title: "編集"); editItem.submenu = editMenu
        editMenu.addItem(withTitle: "元に戻す", action: Selector(("undo:")), keyEquivalent: "z")
        editMenu.addItem(.separator())
        editMenu.addItem(withTitle: "カット", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "コピー", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "ペースト", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "すべてを選択", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        let helpItem = NSMenuItem(); main.addItem(helpItem)
        let helpMenu = NSMenu(title: "ヘルプ"); helpItem.submenu = helpMenu
        helpMenu.addItem(withTitle: "使い方", action: #selector(help), keyEquivalent: "?")
        NSApp.mainMenu = main
    }

    func label(_ text: String, size: CGFloat = 12, weight: NSFont.Weight = .regular, color: NSColor = .labelColor) -> NSTextField {
        let view = NSTextField(wrappingLabelWithString: text)
        view.font = .systemFont(ofSize: size, weight: weight)
        view.textColor = color
        view.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        return view
    }
    func vertical(_ views: [NSView], spacing: CGFloat = 9) -> NSStackView {
        let stack = FlippedStackView(views: views)
        stack.orientation = .vertical; stack.alignment = .leading; stack.spacing = spacing
        for view in views { view.translatesAutoresizingMaskIntoConstraints = false; view.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true }
        return stack
    }
    func horizontal(_ views: [NSView], spacing: CGFloat = 8) -> NSStackView {
        let stack = NSStackView(views: views); stack.orientation = .horizontal; stack.spacing = spacing; stack.alignment = .centerY; stack.distribution = .fill
        return stack
    }
    func configure(_ control: NSControl) {
        controls.append(control)
        if let text = control as? NSTextField, text.isEditable { text.delegate = self }
        if let button = control as? NSButton { button.target = self; button.action = #selector(settingsChanged) }
        if let popup = control as? NSPopUpButton { popup.target = self; popup.action = #selector(settingsChanged) }
    }
    func field(_ title: String, _ control: NSControl, hint: String? = nil) -> NSStackView {
        configure(control)
        control.setAccessibilityLabel(title)
        var views: [NSView] = [label(title, weight: .medium), control]
        if let hint = hint { views.append(label(hint, size: 11, color: .secondaryLabelColor)) }
        return vertical(views, spacing: 4)
    }
    func section(_ title: String) -> NSView {
        let line = NSBox(); line.boxType = .separator
        let s = vertical([label(title, size: 13, weight: .semibold), line], spacing: 6)
        return s
    }
    func scroll(_ document: NSView) -> NSScrollView {
        let s = NSScrollView(); s.hasVerticalScroller = true; s.borderType = .noBorder
        s.drawsBackground = false; s.documentView = document; return s
    }

    func buildWindow() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1060, height: 850), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = appName; window.minSize = NSSize(width: 960, height: 760); window.delegate = self
        window.center(); window.setFrameAutosaveName("PDFDropWindow")
        let root = window.contentView!
        let symbol = NSImageView(image: NSImage(systemSymbolName: isSplit ? "rectangle.split.2x1" : "doc.text.magnifyingglass", accessibilityDescription: nil)!)
        symbol.contentTintColor = .controlAccentColor
        symbol.symbolConfiguration = .init(pointSize: 32, weight: .medium)
        symbol.widthAnchor.constraint(equalToConstant: 48).isActive = true
        let intro = vertical([label(appName, size: 25, weight: .bold), label(isSplit ? "見開きページを、読みやすい単ページのPDFに。" : "確認したい注釈ページを、ひとつのPDFに。", size: 13, color: .secondaryLabelColor)], spacing: 4)
        let header = horizontal([symbol, intro], spacing: 12)
        let dropTitle = label("PDF・フォルダをここにドロップ", size: 17, weight: .semibold, color: .controlAccentColor)
        dropTitle.alignment = .center
        let dropSub = label("複数選択に対応。Finderのアプリアイコンへのドロップでも追加できます。", size: 12, color: .secondaryLabelColor)
        dropSub.alignment = .center
        let dropLabels = vertical([dropTitle, dropSub], spacing: 6)
        drop.addSubview(dropLabels); dropLabels.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([dropLabels.centerYAnchor.constraint(equalTo: drop.centerYAnchor), dropLabels.leadingAnchor.constraint(equalTo: drop.leadingAnchor, constant: 20), dropLabels.trailingAnchor.constraint(equalTo: drop.trailingAnchor, constant: -20), drop.heightAnchor.constraint(equalToConstant: 96)])
        drop.receive = { [weak self] urls in self?.add(urls, fromDrop: true) }

        countLabel.font = .systemFont(ofSize: 13, weight: .semibold)
        let addPDF = NSButton(title: "PDFを追加…", target: self, action: #selector(addFiles))
        let addDir = NSButton(title: "フォルダを追加…", target: self, action: #selector(addFolder))
        let remove = NSButton(title: "選択を削除", target: self, action: #selector(removeSelected))
        let clear = NSButton(title: "すべて外す", target: self, action: #selector(clearInputs))
        controls.append(contentsOf: [addPDF, addDir, remove, clear])
        table.headerView = nil; table.rowHeight = 50; table.allowsMultipleSelection = true
        table.style = .inset; table.delegate = self; table.dataSource = self
        table.setDraggingSourceOperationMask(.copy, forLocal: true)
        table.setDraggingSourceOperationMask(.copy, forLocal: false)
        table.setAccessibilityLabel("入力PDFとフォルダ")
        let column = NSTableColumn(identifier: .init("file")); table.addTableColumn(column)
        let tableScroll = scroll(table); tableScroll.borderType = .bezelBorder; tableScroll.heightAnchor.constraint(equalToConstant: 164).isActive = true
        let queueButtons = horizontal([addPDF, addDir, NSView(), remove, clear], spacing: 6)
        log.isEditable = false; log.isSelectable = true; log.font = .systemFont(ofSize: 12)
        log.textContainerInset = NSSize(width: 10, height: 10)
        log.autoresizingMask = [.width]; log.isHorizontallyResizable = false; log.isVerticallyResizable = true
        log.textContainer?.widthTracksTextView = true
        log.setAccessibilityLabel("処理結果")
        let logScroll = scroll(log); logScroll.borderType = .bezelBorder
        logScroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 120).isActive = true
        let logButtons = horizontal([revealButton, NSView(), NSButton(title: "結果を保存…", target: self, action: #selector(saveLog))])
        revealButton.target = self; revealButton.action = #selector(revealOutputs); revealButton.isEnabled = false
        let left = vertical([countLabel, tableScroll, queueButtons, section("処理結果"), logScroll, logButtons], spacing: 10)

        outputMode.addItems(withTitles: ["元PDFの場所に専用フォルダを作る", "指定したフォルダに保存"])
        outputPath.font = .systemFont(ofSize: 11); outputPath.textColor = .secondaryLabelColor
        outputPath.lineBreakMode = .byTruncatingMiddle; outputPath.maximumNumberOfLines = 1
        chooseOutput.target = self; chooseOutput.action = #selector(selectOutput)
        controls.append(chooseOutput)
        suffix.placeholderString = isSplit ? "_split" : "_extract"
        existing.addItems(withTitles: ["連番を付けて保存（おすすめ）", "スキップ", "上書き"])
        pages.placeholderString = "空欄＝全ページ　例：1-5,8,12-"
        order.addItems(withTitles: ["左 → 右", "右 → 左（右綴じ）"])
        scope.addItems(withTitles: ["横長のページだけ分割", "すべてのページを分割"])
        ratio.placeholderString = "50"; dpi.placeholderString = "144"
        mode.addItems(withTitles: ["文字・注釈を編集可能なまま保存", "見た目を画像として保存"])
        spanning.addItems(withTitles: ["両側に残す", "左側だけに残す", "右側だけに残す", "両側から削除"])
        filter.addItems(withTitles: ["すべての注釈", "コメント本文がある注釈だけ", "手書き注釈だけ"])
        context.addItems(withTitles: (0...5).map { $0 == 0 ? "追加しない" : "前後に各\($0)ページ追加" })
        password.placeholderString = "必要な場合だけ入力（保存しません）"
        var settingViews: [NSView] = [section("保存先"), field("保存場所", outputMode), outputPath, chooseOutput,
          field("ファイル名の末尾", suffix), field("同名ファイルがある場合", existing), section(isSplit ? "分割の設定" : "抽出の設定")]
        if isSplit {
            settingViews += [field("分割するページ", scope), field("分割後のページ順", order), field("左側の幅（%）", ratio, hint: "10〜90。50で中央から分割します。"),
              field("境界をまたぐ注釈", spanning)]
        } else {
            settingViews += [field("ページを選ぶ条件", filter, hint: "付箋・ハイライト・手書きなどが対象。リンクと入力欄は判定に含めません。"),
              field("確認用に前後のページを含める", context)]
        }
        settingViews += [field("PDFの保存方法", mode, hint: "画像保存では文字検索・注釈の編集ができなくなります。"), field("画像保存の解像度（dpi）", dpi, hint: "72〜600。通常は144〜200程度。"), section("共通の設定"),
          field("対象ページ（元PDFの通し番号）", pages), recursive, field("PDFのパスワード", password), autoRun, showFinder]
        for c in [recursive, autoRun, showFinder] { configure(c) }
        infoLabel = label("設定は次回も引き継ぎます。入力PDFは変更しません。", size: 11, color: .secondaryLabelColor)
        settingViews.append(infoLabel)
        settingsStack = vertical(settingViews, spacing: 11)
        let settingsScroll = scroll(settingsStack)
        settingsStack.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([settingsStack.topAnchor.constraint(equalTo: settingsScroll.contentView.topAnchor), settingsStack.leadingAnchor.constraint(equalTo: settingsScroll.contentView.leadingAnchor), settingsStack.trailingAnchor.constraint(equalTo: settingsScroll.contentView.trailingAnchor, constant: -14)])
        settingsScroll.widthAnchor.constraint(equalToConstant: 365).isActive = true
        let divider = NSBox(); divider.boxType = .separator
        divider.widthAnchor.constraint(equalToConstant: 1).isActive = true
        let middle = horizontal([left, divider, settingsScroll], spacing: 22); middle.alignment = .top
        left.widthAnchor.constraint(equalTo: middle.widthAnchor, constant: -410).isActive = true
        left.heightAnchor.constraint(equalTo: middle.heightAnchor).isActive = true
        divider.heightAnchor.constraint(equalTo: middle.heightAnchor).isActive = true
        settingsScroll.heightAnchor.constraint(equalTo: middle.heightAnchor).isActive = true
        progress.isIndeterminate = false; progress.minValue = 0; progress.maxValue = 100; progress.style = .bar
        progress.heightAnchor.constraint(equalToConstant: 6).isActive = true
        status.font = .systemFont(ofSize: 12); status.lineBreakMode = .byTruncatingMiddle
        startButton.bezelStyle = .rounded; startButton.keyEquivalent = "\r"; startButton.controlSize = .large
        startButton.target = self; startButton.action = #selector(start)
        cancelButton.target = self; cancelButton.action = #selector(stop); cancelButton.isEnabled = false
        let footer = horizontal([status, NSView(), cancelButton, startButton], spacing: 12)
        let all = vertical([header, drop, middle, progress, footer], spacing: 18)
        root.addSubview(all); all.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([all.leadingAnchor.constraint(equalTo: root.leadingAnchor, constant: 26), all.trailingAnchor.constraint(equalTo: root.trailingAnchor, constant: -26), all.topAnchor.constraint(equalTo: root.topAnchor, constant: 22), all.bottomAnchor.constraint(equalTo: root.bottomAnchor, constant: -22)])
        middle.setContentHuggingPriority(.defaultLow, for: .vertical)
        logScroll.setContentHuggingPriority(.defaultLow, for: .vertical)
    }

    func loadSettings() {
        outputMode.selectItem(at: defaults.integer(forKey: "outputMode"))
        selectedOutput = defaults.string(forKey: "outputPath").map { URL(fileURLWithPath: $0, isDirectory: true) }
        outputPath.stringValue = selectedOutput?.path ?? "保存先が未指定です"
        suffix.stringValue = defaults.string(forKey: "suffix") ?? (isSplit ? "_split" : "_extract")
        existing.selectItem(at: defaults.integer(forKey: "existing"))
        order.selectItem(at: defaults.integer(forKey: "order"))
        scope.selectItem(at: defaults.integer(forKey: "scope"))
        ratio.stringValue = defaults.string(forKey: "ratio") ?? "50"
        mode.selectItem(at: defaults.integer(forKey: "mode"))
        dpi.stringValue = defaults.string(forKey: "dpi") ?? "144"
        spanning.selectItem(at: defaults.integer(forKey: "spanning"))
        filter.selectItem(at: defaults.integer(forKey: "filter"))
        context.selectItem(at: defaults.integer(forKey: "context"))
        pages.stringValue = defaults.string(forKey: "pages") ?? ""
        recursive.state = defaults.bool(forKey: "recursive") ? .on : .off
        autoRun.state = defaults.bool(forKey: "autoRun") ? .on : .off
        showFinder.state = (defaults.object(forKey: "showFinder") == nil || defaults.bool(forKey: "showFinder")) ? .on : .off
    }
    func saveSettings() {
        for (key, value) in ["outputMode": outputMode.indexOfSelectedItem, "existing": existing.indexOfSelectedItem, "order": order.indexOfSelectedItem, "scope": scope.indexOfSelectedItem, "mode": mode.indexOfSelectedItem, "spanning": spanning.indexOfSelectedItem, "filter": filter.indexOfSelectedItem, "context": context.indexOfSelectedItem] { defaults.set(value, forKey: key) }
        for (key, value) in ["suffix": suffix.stringValue, "ratio": ratio.stringValue, "dpi": dpi.stringValue, "pages": pages.stringValue] { defaults.set(value, forKey: key) }
        for (key, value) in ["recursive": recursive.state == .on, "autoRun": autoRun.state == .on, "showFinder": showFinder.state == .on] { defaults.set(value, forKey: key) }
        defaults.set(selectedOutput?.path, forKey: "outputPath")
    }
    @objc func settingsChanged() { saveSettings(); refreshEnabled() }
    func controlTextDidChange(_ obj: Notification) { saveSettings() }
    func refreshEnabled() {
        for control in controls { control.isEnabled = !isRunning }
        dpi.isEnabled = !isRunning && mode.indexOfSelectedItem == 1
        chooseOutput.isEnabled = !isRunning && outputMode.indexOfSelectedItem == 1
        outputPath.stringValue = outputMode.indexOfSelectedItem == 1 ? (selectedOutput?.path ?? "保存先が未指定です") : "入力の場所に「\(isSplit ? "分割PDF" : "注釈抽出PDF")」フォルダを作成"
        outputPath.textColor = outputMode.indexOfSelectedItem == 1 ? .secondaryLabelColor : .tertiaryLabelColor
        startButton.isEnabled = !isRunning && !inputs.isEmpty
        cancelButton.isEnabled = isRunning && !cancelled
        drop.busy = isRunning
        revealButton.isEnabled = !outputs.isEmpty
    }

    func numberOfRows(in tableView: NSTableView) -> Int { inputs.count }
    func tableView(_ tableView: NSTableView, pasteboardWriterForRow row: Int) -> NSPasteboardWriting? {
        isRunning ? nil : inputs[row] as NSURL
    }
    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
        let url = inputs[row]
        let icon = NSImageView(image: NSWorkspace.shared.icon(forFile: url.path))
        icon.widthAnchor.constraint(equalToConstant: 26).isActive = true; icon.heightAnchor.constraint(equalToConstant: 30).isActive = true
        let name = label(url.lastPathComponent, size: 12, weight: .medium); name.maximumNumberOfLines = 1; name.lineBreakMode = .byTruncatingMiddle
        let path = label(url.deletingLastPathComponent().path, size: 10, color: .secondaryLabelColor); path.maximumNumberOfLines = 1; path.lineBreakMode = .byTruncatingMiddle
        let row = horizontal([icon, vertical([name, path], spacing: 2)])
        row.setAccessibilityLabel(url.path)
        return row
    }
    func add(_ urls: [URL], fromDrop: Bool) {
        guard !isRunning else { alert("処理中です", "完了または中止してから追加してください。"); return }
        var rejected: [String] = []
        for url in urls {
            let url = url.standardizedFileURL.resolvingSymlinksInPath()
            let isDir = (try? url.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true
            if isDir || url.pathExtension.lowercased() == "pdf" {
                if !inputs.contains(url) { inputs.append(url) }
            } else { rejected.append(url.lastPathComponent) }
        }
        table.reloadData(); countLabel.stringValue = "入力 \(inputs.count)件"
        status.stringValue = "\(inputs.count)件を追加済み。設定を確認して「処理を開始」を押してください。"
        refreshEnabled()
        if !rejected.isEmpty { alert("PDFまたはフォルダを追加してください", rejected.joined(separator: "\n")) }
        if fromDrop && autoRun.state == .on && !inputs.isEmpty { start() }
    }
    @objc func addFiles() {
        guard !isRunning else { return }
        let panel = NSOpenPanel(); panel.canChooseDirectories = false; panel.canChooseFiles = true; panel.allowsMultipleSelection = true; panel.allowedContentTypes = [.pdf]
        panel.message = "処理するPDFを選んでください。"
        panel.beginSheetModal(for: window) { [weak self] result in if result == .OK { self?.add(panel.urls, fromDrop: false) } }
    }
    @objc func addFolder() {
        guard !isRunning else { return }
        let panel = NSOpenPanel(); panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.allowsMultipleSelection = true
        panel.message = "PDFが入ったフォルダを選んでください。"
        panel.beginSheetModal(for: window) { [weak self] result in if result == .OK { self?.add(panel.urls, fromDrop: false) } }
    }
    @objc func removeSelected() {
        guard !isRunning else { return }
        for i in table.selectedRowIndexes.reversed() { inputs.remove(at: i) }
        table.reloadData(); countLabel.stringValue = "入力 \(inputs.count)件"; refreshEnabled()
    }
    @objc func clearInputs() {
        guard !isRunning else { return }
        inputs = []; table.reloadData(); countLabel.stringValue = "入力 0件"; refreshEnabled()
    }
    @objc func selectOutput() {
        let panel = NSOpenPanel(); panel.canChooseFiles = false; panel.canChooseDirectories = true; panel.canCreateDirectories = true
        panel.directoryURL = selectedOutput; panel.message = "作成するPDFの保存先を選んでください。"
        panel.beginSheetModal(for: window) { [weak self] response in
            guard let self = self, response == .OK, let url = panel.url else { return }
            self.selectedOutput = url; self.outputPath.stringValue = url.path; self.saveSettings()
        }
    }
    @objc func start() {
        guard !isRunning, !inputs.isEmpty else { return }
        if outputMode.indexOfSelectedItem == 1 && selectedOutput == nil { selectOutput(); return }
        let end = suffix.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !end.isEmpty, end.count <= 80, !end.contains(where: { "/:\\".contains($0) || $0.asciiValue.map { $0 < 32 } == true }) else { alert("ファイル名の末尾を確認してください", "1〜80文字で、 / : \\ を含めず指定してください。"); return }
        let ratioValue = isSplit ? (Double(ratio.stringValue) ?? 0) : 50
        guard (10...90).contains(ratioValue) else { alert("分割位置を確認してください", "10〜90の数値で指定してください。"); return }
        let dpiValue = mode.indexOfSelectedItem == 1 ? (Int(dpi.stringValue) ?? 0) : 144
        guard (72...600).contains(dpiValue) else { alert("解像度を確認してください", "72〜600の整数で指定してください。"); return }
        let request: [String: Any] = ["operation": operation, "inputs": inputs.map(\.path), "output_dir": outputMode.indexOfSelectedItem == 1 ? selectedOutput!.path : "",
            "suffix": end, "existing": ["rename", "skip", "overwrite"][max(0, existing.indexOfSelectedItem)], "recursive": recursive.state == .on,
            "pages": pages.stringValue, "order": order.indexOfSelectedItem == 1 ? "R" : "L", "split_all": scope.indexOfSelectedItem == 1,
            "ratio": ratioValue, "mode": mode.indexOfSelectedItem == 1 ? "flatten" : "keep", "dpi": dpiValue,
            "spanning": ["both", "left", "right", "drop"][max(0, spanning.indexOfSelectedItem)], "filter": ["all", "text", "ink"][max(0, filter.indexOfSelectedItem)],
            "context": max(0, context.indexOfSelectedItem), "password": password.stringValue]
        guard let data = try? JSONSerialization.data(withJSONObject: request), let engineURL = Bundle.main.resourceURL?.appendingPathComponent("Engine/pdf-engine"), FileManager.default.isExecutableFile(atPath: engineURL.path) else { alert("処理を開始できません", "アプリ内の処理プログラムが見つかりません。アプリ一式を作り直してください。"); return }
        saveSettings(); logText = ""; log.string = ""; outputs = []; counts = ["ok": 0, "skip": 0, "error": 0]; fatalMessage = nil; cancelled = false
        isRunning = true; progress.doubleValue = 0; status.stringValue = "PDFを確認しています…"; refreshEnabled()
        appendLog("\(appName)　\(DateFormatter.localizedString(from: Date(), dateStyle: .medium, timeStyle: .short))\n")
        let task = Process(); task.executableURL = engineURL
        let input = Pipe(), output = Pipe(), error = Pipe()
        task.standardInput = input; task.standardOutput = output; task.standardError = error
        var environment = ProcessInfo.processInfo.environment
        for key in ["PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH"] { environment.removeValue(forKey: key) }
        environment["PYTHONUNBUFFERED"] = "1"
        task.environment = environment; process = task
        do { try task.run() } catch { isRunning = false; process = nil; refreshEnabled(); alert("起動できませんでした", error.localizedDescription); return }
        // Drain stderr continuously so warnings cannot block the PDF worker.
        error.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if !data.isEmpty, let text = String(data: data, encoding: .utf8) {
                DispatchQueue.main.async { [weak self] in self?.appendLog("補足: \(text.trimmingCharacters(in: .whitespacesAndNewlines))\n") }
            }
        }
        input.fileHandleForWriting.write(data); try? input.fileHandleForWriting.close()
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            var buffer = Data()
            while true {
                let chunk = output.fileHandleForReading.availableData
                if chunk.isEmpty { break }
                buffer.append(chunk)
                while let boundary = buffer.firstIndex(of: 10) {
                    let line = Data(buffer[..<boundary]); buffer.removeSubrange(...boundary)
                    if let object = try? JSONSerialization.jsonObject(with: line) as? [String: Any] {
                        DispatchQueue.main.async { self?.handle(object) }
                    } else if let text = String(data: line, encoding: .utf8), !text.isEmpty {
                        DispatchQueue.main.async { self?.appendLog("補足: \(text)\n") }
                    }
                }
            }
            task.waitUntilExit(); error.fileHandleForReading.readabilityHandler = nil
            let code = task.terminationStatus
            DispatchQueue.main.async { self?.finish(code: code) }
        }
    }
    func handle(_ event: [String: Any]) {
        switch event["event"] as? String {
        case "file": status.stringValue = "\(event["index"] ?? 0) / \(event["total"] ?? 0)　\(event["name"] ?? "")"
        case "progress":
            let index = event["file_index"] as? Double ?? 1, total = event["total"] as? Double ?? 1
            let page = event["page"] as? Double ?? 0, pageCount = event["pages"] as? Double ?? 1
            let flattening = (event["phase"] as? String) == "flatten"
            let fraction = page / max(1, pageCount)
            let part = mode.indexOfSelectedItem == 1 ? (flattening ? 0.5 + fraction * 0.5 : fraction * 0.5) : fraction
            progress.doubleValue = ((index - 1 + part) / max(1, total)) * 100
            status.stringValue = "\(Int(index)) / \(Int(total))　\(event["name"] ?? "")　\(flattening ? "画像化" : "処理") \(Int(page)) / \(Int(pageCount))ページ"
        case "result":
            let kind = event["status"] as? String ?? "error"
            counts[kind, default: 0] += 1
            let source = URL(fileURLWithPath: event["input"] as? String ?? "")
            let marker = kind == "ok" ? "✓ 保存" : kind == "skip" ? "– スキップ" : "! エラー"
            appendLog("\(marker)　\(source.lastPathComponent)\n\(event["message"] as? String ?? "")\n")
            if let path = event["output"] as? String { outputs.append(URL(fileURLWithPath: path)); appendLog("保存先: \(path)\n") }
            if let matched = event["matched"] as? [Int] { appendLog("注釈がある元ページ: \(matched.map(String.init).joined(separator: ", "))\n") }
            if let selected = event["selected"] as? [Int], selected != event["matched"] as? [Int] { appendLog("保存した元ページ: \(selected.map(String.init).joined(separator: ", "))\n") }
            if kind == "error" { appendLog("入力: \(source.path)\n") }
            appendLog("\n")
        case "fatal": fatalMessage = event["message"] as? String; appendLog("エラー: \(fatalMessage ?? "処理に失敗しました。")\n")
        case "cancelled": cancelled = true
        default: break
        }
    }
    func finish(code: Int32) {
        isRunning = false; process = nil; password.stringValue = ""
        if code != 0 && !cancelled && fatalMessage == nil { fatalMessage = "処理プログラムが終了しました（\(code)）。"; appendLog(fatalMessage! + "\n") }
        let summary = "保存 \(counts["ok", default: 0])件 ／ スキップ \(counts["skip", default: 0])件 ／ エラー \(counts["error", default: 0])件"
        status.stringValue = cancelled ? "中止しました。\(summary)" : fatalMessage != nil ? "処理を完了できませんでした。結果欄を確認してください。" : "完了　\(summary)"
        appendLog(status.stringValue + "\n")
        if !cancelled && fatalMessage == nil { progress.doubleValue = 100 }
        refreshEnabled()
        if showFinder.state == .on && !outputs.isEmpty { revealOutputs() }
        NSApp.requestUserAttention(.informationalRequest)
    }
    @objc func stop() {
        guard isRunning else { return }
        cancelled = true; status.stringValue = "中止しています… 保存済みのPDFは残します。"; refreshEnabled(); process?.terminate()
    }
    func appendLog(_ text: String) {
        logText += text
        log.textStorage?.append(NSAttributedString(string: text, attributes: [.font: NSFont.systemFont(ofSize: 12), .foregroundColor: NSColor.labelColor]))
        log.scrollToEndOfDocument(nil)
    }
    @objc func revealOutputs() { if !outputs.isEmpty { NSWorkspace.shared.activateFileViewerSelecting(Array(outputs.prefix(50))) } }
    @objc func saveLog() {
        guard !logText.isEmpty else { alert("保存する結果がありません", "処理後に実行してください。"); return }
        let panel = NSSavePanel(); panel.allowedContentTypes = [.plainText]; panel.nameFieldStringValue = appName + "_結果.txt"
        panel.beginSheetModal(for: window) { [weak self] response in
            guard let self = self, response == .OK, let url = panel.url else { return }
            do { try self.logText.write(to: url, atomically: true, encoding: .utf8) }
            catch { self.alert("結果を保存できませんでした", error.localizedDescription) }
        }
    }
    func alert(_ title: String, _ message: String) {
        let alert = NSAlert(); alert.messageText = title; alert.informativeText = message; alert.addButton(withTitle: "OK")
        if window != nil { alert.beginSheetModal(for: window) } else { alert.runModal() }
    }
    @objc func focusSettings() { window.makeFirstResponder(suffix); suffix.scrollToVisible(suffix.bounds) }
    @objc func about() { NSApp.orderFrontStandardAboutPanel(options: [.applicationName: appName, .applicationVersion: "1.0", .credits: NSAttributedString(string: "元のPDFスクリプトをMac用のD&Dアプリに再構成。\nPDF処理: PyMuPDF（アプリ内に同梱）\nApple Silicon / macOS 13以降")]) }
    @objc func help() {
        alert("使い方", "1. PDFまたはフォルダをドロップします。\n2. 保存先と処理の設定を確認します。\n3. 「処理を開始」を押します。\n\n初期設定では元PDFの隣に専用フォルダを作成します。同名の出力には連番が付きます。\n\n設定は次回も引き継ぎます。パスワードは記憶しません。\n対象ページは元PDFの通し番号です。空欄で全ページ。\n\n詳細は同梱の「使い方.md」を参照してください。")
    }
}
