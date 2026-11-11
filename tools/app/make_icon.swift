// Draws the Trading Desk icon: 12 overlapping circles in a ring, in iOS blue on
// black, as a 1024 x 1024 PNG.
// Usage: swift make_icon.swift out.png
import AppKit

let size: CGFloat = 1024
let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "icon.png"
let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(size), pixelsHigh: Int(size),
                           bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                           colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)

// macOS icon grid: a rounded square inset from the canvas
let inset: CGFloat = 100
let tile = NSRect(x: inset, y: inset, width: size - 2 * inset, height: size - 2 * inset)
let shape = NSBezierPath(roundedRect: tile, xRadius: 185, yRadius: 185)
NSGradient(starting: NSColor(calibratedWhite: 0.13, alpha: 1), ending: NSColor(calibratedWhite: 0.02, alpha: 1))!
    .draw(in: shape, angle: -90)
NSColor(calibratedWhite: 1, alpha: 0.08).setStroke()
shape.lineWidth = 3
shape.stroke()

// iOS dark-mode blue, the dashboard's accent (#0A84FF)
let lime = NSColor(calibratedRed: 10 / 255, green: 132 / 255, blue: 255 / 255, alpha: 1)
// the same proportions as the 24-unit mark on the dashboard, scaled up
let unit: CGFloat = 26
let cx = size / 2 - 0.0 * unit, cy = size / 2 + 0.5 * unit
for i in 0..<12 {
    let a = CGFloat(i) / 12 * 2 * .pi
    let x = cx + 4.6 * unit * cos(a), y = cy + 4.6 * unit * sin(a)
    let r = 4.4 * unit
    let c = NSBezierPath(ovalIn: NSRect(x: x - r, y: y - r, width: 2 * r, height: 2 * r))
    lime.withAlphaComponent(0.88).setStroke()
    c.lineWidth = 0.7 * unit
    c.stroke()
}

NSGraphicsContext.restoreGraphicsState()
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: out))
print("wrote", out)
