import AppKit

let output = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
for (name, symbol, color) in [("split", "rectangle.split.2x1.fill", NSColor.systemBlue), ("extract", "text.bubble.fill", NSColor.systemTeal)] {
    let folder = output.appendingPathComponent(name + ".iconset")
    try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    for points in [16, 32, 128, 256, 512] {
        for scale in [1, 2] {
            let size = points * scale
            let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size, bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
            NSGraphicsContext.saveGraphicsState()
            NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
            let s = CGFloat(size)
            let outer = NSBezierPath(roundedRect: NSRect(x: s * 0.07, y: s * 0.07, width: s * 0.86, height: s * 0.86), xRadius: s * 0.19, yRadius: s * 0.19)
            NSGradient(starting: color.blended(withFraction: 0.13, of: .white)!, ending: color.blended(withFraction: 0.16, of: .black)!)!.draw(in: outer, angle: -90)
            let image = NSImage(systemSymbolName: symbol, accessibilityDescription: nil)!.withSymbolConfiguration(.init(pointSize: s * 0.44, weight: .medium))!
            let tint = NSImage(size: image.size)
            tint.lockFocus(); image.draw(at: .zero, from: .zero, operation: .sourceOver, fraction: 1)
            NSColor.white.set(); NSRect(origin: .zero, size: image.size).fill(using: .sourceAtop); tint.unlockFocus()
            let w = s * 0.57, h = w * image.size.height / image.size.width
            tint.draw(in: NSRect(x: (s - w) / 2, y: (s - h) / 2 + s * 0.04, width: w, height: h))
            if size >= 128 {
                let text = "PDF" as NSString
                let attributes: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: s * 0.105, weight: .bold), .foregroundColor: NSColor.white.withAlphaComponent(0.94)]
                let measured = text.size(withAttributes: attributes)
                text.draw(at: NSPoint(x: (s - measured.width) / 2, y: s * 0.16), withAttributes: attributes)
            }
            NSGraphicsContext.restoreGraphicsState()
            let suffix = scale == 2 ? "@2x" : ""
            try rep.representation(using: .png, properties: [:])!.write(to: folder.appendingPathComponent("icon_\(points)x\(points)\(suffix).png"))
        }
    }
}
