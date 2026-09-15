import AppKit
import Foundation

private let outputNames = [
    (16, "icon_16x16.png"),
    (32, "icon_16x16@2x.png"),
    (32, "icon_32x32.png"),
    (64, "icon_32x32@2x.png"),
    (128, "icon_128x128.png"),
    (256, "icon_128x128@2x.png"),
    (256, "icon_256x256.png"),
    (512, "icon_256x256@2x.png"),
    (512, "icon_512x512.png"),
    (1024, "icon_512x512@2x.png"),
]

private func scaled(_ value: CGFloat, in size: CGFloat) -> CGFloat {
    value * size / 1024
}

private func rect(_ x: CGFloat, _ y: CGFloat, _ width: CGFloat, _ height: CGFloat, size: CGFloat) -> NSRect {
    NSRect(x: scaled(x, in: size), y: scaled(y, in: size), width: scaled(width, in: size), height: scaled(height, in: size))
}

private func drawIcon(size: Int) -> NSImage {
    let edge = CGFloat(size)
    let image = NSImage(size: NSSize(width: edge, height: edge))
    image.lockFocus()
    defer { image.unlockFocus() }

    let background = NSBezierPath(roundedRect: rect(40, 40, 944, 944, size: edge), xRadius: scaled(220, in: edge), yRadius: scaled(220, in: edge))
    NSGradient(
        starting: NSColor(calibratedRed: 0.10, green: 0.24, blue: 0.21, alpha: 1),
        ending: NSColor(calibratedRed: 0.04, green: 0.12, blue: 0.10, alpha: 1)
    )?.draw(in: background, angle: -45)

    let border = NSBezierPath(roundedRect: rect(60, 60, 904, 904, size: edge), xRadius: scaled(200, in: edge), yRadius: scaled(200, in: edge))
    NSColor(calibratedRed: 0.42, green: 0.90, blue: 0.72, alpha: 0.30).setStroke()
    border.lineWidth = scaled(12, in: edge)
    border.stroke()

    let ringRect = rect(238, 228, 548, 548, size: edge)
    let ring = NSBezierPath(ovalIn: ringRect)
    NSColor(calibratedRed: 0.91, green: 0.98, blue: 0.95, alpha: 1).setStroke()
    ring.lineWidth = scaled(56, in: edge)
    ring.stroke()

    let separator = NSBezierPath()
    separator.move(to: NSPoint(x: scaled(259, in: edge), y: scaled(502, in: edge)))
    separator.line(to: NSPoint(x: scaled(765, in: edge), y: scaled(502, in: edge)))
    separator.lineWidth = scaled(56, in: edge)
    separator.stroke()

    let center = NSBezierPath(ovalIn: rect(420, 410, 184, 184, size: edge))
    NSColor(calibratedRed: 0.06, green: 0.19, blue: 0.16, alpha: 1).setFill()
    center.fill()
    NSColor(calibratedRed: 0.91, green: 0.98, blue: 0.95, alpha: 1).setStroke()
    center.lineWidth = scaled(42, in: edge)
    center.stroke()

    NSGraphicsContext.saveGraphicsState()
    let transform = NSAffineTransform()
    transform.translateX(by: scaled(720, in: edge), yBy: scaled(695, in: edge))
    transform.rotate(byDegrees: -45)
    transform.concat()
    let pencil = NSBezierPath(roundedRect: NSRect(x: -scaled(25, in: edge), y: -scaled(94, in: edge), width: scaled(50, in: edge), height: scaled(188, in: edge)), xRadius: scaled(14, in: edge), yRadius: scaled(14, in: edge))
    NSColor(calibratedRed: 1.0, green: 0.79, blue: 0.40, alpha: 1).setFill()
    pencil.fill()
    NSColor(calibratedRed: 1.0, green: 0.96, blue: 0.85, alpha: 1).setStroke()
    pencil.lineWidth = scaled(12, in: edge)
    pencil.stroke()
    NSGraphicsContext.restoreGraphicsState()

    return image
}

guard CommandLine.arguments.count == 2 else {
    fputs("Usage: CreateIcon <iconset-directory>\\n", stderr)
    exit(64)
}

let destination = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
for (pixels, name) in outputNames {
    guard let tiff = drawIcon(size: pixels).tiffRepresentation,
          let bitmap = NSBitmapImageRep(data: tiff),
          let png = bitmap.representation(using: .png, properties: [:]) else {
        fputs("Unable to render app icon.\\n", stderr)
        exit(1)
    }
    try png.write(to: destination.appendingPathComponent(name))
}
