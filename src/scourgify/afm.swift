// Apple Foundation Models bridge for calibre-wrangler (macOS 26+, Apple Intelligence).
// Persistent stdin loop: each input line is one prompt (literal newlines escaped as );
// prints one response line per prompt. Used by classify.py --engine apple.
//   build:  swiftc -O afm.swift -o afm     (or run directly: swift afm.swift)
import Foundation
import FoundationModels

@available(macOS 26.0, *)
func loop() async {
    guard case .available = SystemLanguageModel.default.availability else {
        FileHandle.standardError.write(Data("AFM_UNAVAILABLE\n".utf8)); exit(2)
    }
    while let line = readLine(strippingNewline: true) {
        let prompt = line.replacingOccurrences(of: "\u{01}", with: "\n")
        var out = "ERR"
        do {
            let resp = try await LanguageModelSession().respond(to: prompt)
            out = resp.content.replacingOccurrences(of: "\n", with: " ")
        } catch { out = "ERR: \(error)".replacingOccurrences(of: "\n", with: " ") }
        print(out); fflush(stdout)
    }
}

if #available(macOS 26.0, *) {
    let sem = DispatchSemaphore(value: 0)
    Task { await loop(); sem.signal() }
    sem.wait()
} else {
    FileHandle.standardError.write(Data("needs macOS 26+\n".utf8)); exit(1)
}
