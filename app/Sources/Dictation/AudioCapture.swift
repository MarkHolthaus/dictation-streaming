import AVFoundation
import Foundation

/// Captures the mic, converts to 16 kHz mono Int16, and emits about 100 ms chunks.
/// When the engine is kept warm, a short pre-roll is kept so the first words are not cut off.
final class AudioCapture: @unchecked Sendable {
    /// Called on the main queue with raw little-endian PCM16 bytes.
    var onChunk: (@Sendable (Data) -> Void)?

    private var engine = AVAudioEngine() // replaced with a fresh one after a failed start (main thread only)
    private let outFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000, channels: 1, interleaved: true)!
    private var converter: AVAudioConverter? // tap thread only
    private let lock = NSLock()
    private var pending = Data()
    private var preroll: [Data] = []
    private var capturing = false
    private var running = false // main thread only
    private let chunkBytes = 3200 // 1600 samples = 100 ms
    private let prerollChunks = 4

    init() {
        // object: nil so the observer survives replacing `engine`; the handler checks identity.
        NotificationCenter.default.addObserver(forName: .AVAudioEngineConfigurationChange, object: nil, queue: .main) { [weak self] note in
            guard let self, self.running, (note.object as? AVAudioEngine) === self.engine else { return }
            // The default mic changed (AirPods, USB mic, sample rate). Rebuild on the new format.
            self.stopEngine()
            self.restartWithRetries(attempt: 0)
        }
    }

    /// A new device is often not ready yet: retry with growing delays (0.5, 1, 2 s) before giving up.
    private func restartWithRetries(attempt: Int) {
        if (try? startEngine()) != nil { return }
        guard attempt < 3 else { return }
        let delay = 0.5 * pow(2.0, Double(attempt))
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self, !self.running else { return }
            self.restartWithRetries(attempt: attempt + 1)
        }
    }

    /// Plain words for the overlay instead of "com.apple.coreaudio.avfaudio error -10868".
    static func friendlyMessage(for error: Error) -> String {
        let ns = error as NSError
        if ns.domain == "Dictation" { return ns.localizedDescription }
        if ns.code == -10868 || ns.code == -10875 || ns.code == -10877 {
            return "Mic not ready (audio device changed). Try again in a second."
        }
        return "Mic error. Try again."
    }

    /// Main thread only. Engine calls stay outside `lock` so removeTap/stop can never wait on a
    /// tap callback that is itself waiting for the lock. A failed start (format not supported,
    /// -10868) leaves the AVAudioEngine unusable, so it is replaced with a new one and tried once more.
    func startEngine() throws {
        if running { return }
        do {
            try startOnce()
        } catch let first as NSError where first.domain != "Dictation" {
            engine = AVAudioEngine()
            converter = nil
            try startOnce()
        }
    }

    private func startOnce() throws {
        let input = engine.inputNode
        let inFormat = input.outputFormat(forBus: 0)
        guard inFormat.sampleRate > 0, inFormat.channelCount > 0 else {
            throw NSError(domain: "Dictation", code: 1, userInfo: [NSLocalizedDescriptionKey: "No microphone input"])
        }
        // nil format: take whatever the node delivers. A stale explicit format raises an
        // Objective-C exception (crash) when the hardware rate just changed.
        input.installTap(onBus: 0, bufferSize: 2048, format: nil) { [weak self] buffer, _ in
            self?.process(buffer)
        }
        engine.prepare()
        do {
            try engine.start()
        } catch {
            input.removeTap(onBus: 0)
            throw error
        }
        running = true
    }

    func stopEngine() {
        if !running { return }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        running = false
        lock.lock()
        pending.removeAll()
        preroll.removeAll()
        lock.unlock()
    }

    var isRunning: Bool { running }

    func beginCapture() throws {
        try startEngine()
        lock.lock()
        capturing = true
        let old = preroll
        preroll.removeAll()
        for chunk in old { emit(chunk) }
        lock.unlock()
    }

    /// Flushes the tail. The caller should send `stop` via DispatchQueue.main.async afterwards so it
    /// lands behind the last chunk. On a normal release the Coordinator calls this about 250 ms
    /// after key-up, so audio still in flight at release is not lost.
    func endCapture(keepWarm: Bool) {
        lock.lock()
        capturing = false
        if !pending.isEmpty { emit(pending); pending.removeAll() }
        lock.unlock()
        if !keepWarm { stopEngine() }
    }

    // Lock must be held.
    private func emit(_ chunk: Data) {
        DispatchQueue.main.async { [weak self] in self?.onChunk?(chunk) }
    }

    // Audio tap thread only.
    private func process(_ buffer: AVAudioPCMBuffer) {
        if converter == nil || converter?.inputFormat != buffer.format {
            converter = AVAudioConverter(from: buffer.format, to: outFormat)
        }
        guard let converter else { return }
        let ratio = 16000.0 / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio + 64)
        guard let out = AVAudioPCMBuffer(pcmFormat: outFormat, frameCapacity: capacity) else { return }
        var supplied = false
        var error: NSError?
        converter.convert(to: out, error: &error) { _, status in
            if supplied { status.pointee = .noDataNow; return nil }
            supplied = true
            status.pointee = .haveData
            return buffer
        }
        guard error == nil, out.frameLength > 0, let ch = out.int16ChannelData else { return }
        let bytes = Data(bytes: ch[0], count: Int(out.frameLength) * 2)

        lock.lock()
        pending.append(bytes)
        while pending.count >= chunkBytes {
            let chunk = pending.prefix(chunkBytes)
            pending.removeFirst(chunkBytes)
            if capturing {
                emit(Data(chunk))
            } else {
                preroll.append(Data(chunk))
                if preroll.count > prerollChunks { preroll.removeFirst() }
            }
        }
        lock.unlock()
    }
}
