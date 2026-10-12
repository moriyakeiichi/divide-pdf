import AppKit

final class DragInfo: NSObject, NSDraggingInfo {
    var draggingDestinationWindow: NSWindow? { nil }
    var draggingSourceOperationMask: NSDragOperation { .copy }
    var draggingLocation: NSPoint { .zero }
    var draggedImageLocation: NSPoint { .zero }
    var draggedImage: NSImage? { nil }
    let draggingPasteboard: NSPasteboard
    var draggingSource: Any? { nil }
    var draggingSequenceNumber: Int { 1 }
    var draggingFormation: NSDraggingFormation = .none
    var animatesToDestination = false
    var numberOfValidItemsForDrop = 0
    var springLoadingHighlight: NSSpringLoadingHighlight { .none }
    init(_ board: NSPasteboard) { draggingPasteboard = board }
    func slideDraggedImage(to screenPoint: NSPoint) {}
    override func namesOfPromisedFilesDropped(atDestination dropDestination: URL) -> [String]? { nil }
    func resetSpringLoading() {}
    func enumerateDraggingItems(options: NSDraggingItemEnumerationOptions = [], for view: NSView?, classes classArray: [AnyClass], searchOptions: [NSPasteboard.ReadingOptionKey: Any], using block: (NSDraggingItem, Int, UnsafeMutablePointer<ObjCBool>) -> Void) {}
}

let application = NSApplication.shared
let board = NSPasteboard.withUniqueName()
defer { board.releaseGlobally() }
let urls = [URL(fileURLWithPath: "/tmp/日本語 資料.pdf"), URL(fileURLWithPath: "/tmp/PDF folder", isDirectory: true)]
board.writeObjects(urls.map { $0 as NSURL })
let info = DragInfo(board)
let view = DropView(frame: NSRect(x: 0, y: 0, width: 600, height: 100))
var received: [URL] = []
view.receive = { received = $0 }
precondition(view.draggingEntered(info) == .copy)
precondition(view.prepareForDragOperation(info))
precondition(view.performDragOperation(info))
precondition(received == urls)
print("Native D&D receiver: multiple PDF/folder URLs, Japanese names PASS")
view.busy = true
precondition(view.draggingEntered(info).isEmpty)
precondition(!view.prepareForDragOperation(info))
precondition(!view.performDragOperation(info))
print("Busy-state drop rejection PASS")
view.busy = false
board.clearContents(); board.setString("ordinary text", forType: .string)
precondition(view.draggingEntered(info).isEmpty)
print("Non-file drop rejection PASS")
let delegate = AppDelegate()
delegate.application(application, open: urls)
precondition(delegate.deferredURLs == urls)
print("App-icon open-event queue PASS")
