// Reads the text in an image with Apple's on-device Vision framework. Nothing leaves the Mac.
// Usage: ocr <image file>   ->  {"lines": [{"text", "x", "y", "w", "h"}, ...]} on stdout
// x, y, w, h are fractions of the image, with y measured from the top.
import AppKit
import Foundation
import Vision

func fail(_ message: String) -> Never {
    FileHandle.standardOutput.write("{\"error\": \"\(message)\"}\n".data(using: .utf8)!)
    exit(1)
}

guard CommandLine.arguments.count > 1 else { fail("no image given") }
guard let image = NSImage(contentsOfFile: CommandLine.arguments[1]),
      let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else { fail("that image could not be opened") }

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = false      // tickers and numbers are not words
do {
    try VNImageRequestHandler(cgImage: cg, options: [:]).perform([request])
} catch {
    fail("the text could not be read")
}

var lines: [[String: Any]] = []
for observation in request.results ?? [] {
    guard let best = observation.topCandidates(1).first else { continue }
    let box = observation.boundingBox
    lines.append(["text": best.string, "x": box.minX, "y": 1 - box.maxY, "w": box.width, "h": box.height])
}
let data = try! JSONSerialization.data(withJSONObject: ["lines": lines], options: [])
FileHandle.standardOutput.write(data)
FileHandle.standardOutput.write("\n".data(using: .utf8)!)
