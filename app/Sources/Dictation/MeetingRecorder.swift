import AVFoundation
import ScreenCaptureKit

/// Turns any audio buffer into 16 kHz mono Int16 and emits about 500 ms chunks.
private final class PCMChunker {
    private let outFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000, channels: 1, interleaved: true)!
    private var converter: AVAudioConverter?
    private var pending = Data()
    private let chunkBytes = 16000 // 8000 samples = 500 ms
    private let lock = NSLock()
    let emit: (Data) -> Void

    init(emit: @escaping (Data) -> Void) { self.emit = emit }

    func push(_ buffer: AVAudioPCMBuffer) {
        lock.lock(); defer { lock.unlock() }
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
        pending.append(Data(bytes: ch[0], count: Int(out.frameLength) * 2))
        while pending.count >= chunkBytes {
            emit(Data(pending.prefix(chunkBytes)))
            pending.removeFirst(chunkBytes)
        }
    }

    func flush() {
        lock.lock(); defer { lock.unlock() }
        if !pending.isEmpty { emit(pending); pending.removeAll() }
        converter = nil
    }
}

/// Records the mic and, when allowed, everything the Mac plays (the other side of calls).
/// Has its own AVAudioEngine, so it never interferes with hold-fn dictation.
/// Chunks are delivered on the main queue as (source, PCM16), source = "mic" or "system".
final class MeetingRecorder: NSObject, SCStreamOutput, SCStreamDelegate, @unchecked Sendable {
    var onChunk: (@Sendable (String, Data) -> Void)?

    private let micEngine = AVAudioEngine()
    private var micChunker: PCMChunker?
    private var sysChunker: PCMChunker?
    private var stream: SCStream?
    private let sysQueue = DispatchQueue(label: "meeting.system-audio")
    private(set) var recording = false

    /// Tries system audio first. Returns whether it is on. A denied Screen Recording permission
    /// is not an error: the meeting then records the mic only.
    func startSystemAudio() async -> Bool {
        guard CGPreflightScreenCaptureAccess() || CGRequestScreenCaptureAccess() else { return false }
        do {
            let content = try await SCShareableContent.current
            guard let display = content.displays.first else { return false }
            let filter = SCContentFilter(display: display, excludingWindows: [])
            let cfg = SCStreamConfiguration()
            cfg.capturesAudio = true
            cfg.excludesCurrentProcessAudio = true
            cfg.sampleRate = 48000
            cfg.channelCount = 2
            // Audio only is not offered, so ask for the cheapest possible video and ignore it.
            cfg.width = 2; cfg.height = 2
            cfg.minimumFrameInterval = CMTime(value: 1, timescale: 1)
            let s = SCStream(filter: filter, configuration: cfg, delegate: self)
            try s.addStreamOutput(self, type: .audio, sampleHandlerQueue: sysQueue)
            sysChunker = PCMChunker { [weak self] d in self?.deliver("system", d) }
            try await s.startCapture()
            stream = s
            return true
        } catch {
            sysChunker = nil
            return false
        }
    }

    func startMic() throws {
        let input = micEngine.inputNode
        let fmt = input.outputFormat(forBus: 0)
        guard fmt.sampleRate > 0, fmt.channelCount > 0 else {
            throw NSError(domain: "Dictation", code: 2, userInfo: [NSLocalizedDescriptionKey: "No microphone input"])
        }
        micChunker = PCMChunker { [weak self] d in self?.deliver("mic", d) }
        input.installTap(onBus: 0, bufferSize: 4096, format: nil) { [weak self] buffer, _ in
            self?.micChunker?.push(buffer)
        }
        micEngine.prepare()
        do { try micEngine.start() } catch {
            input.removeTap(onBus: 0)
            throw error
        }
        recording = true
    }

    /// Flushes the tails. Call `onChunk`-consumers' stop message via main.async afterwards.
    func stop() async {
        recording = false
        micEngine.inputNode.removeTap(onBus: 0)
        micEngine.stop()
        micChunker?.flush(); micChunker = nil
        if let s = stream { try? await s.stopCapture() }
        stream = nil
        sysChunker?.flush(); sysChunker = nil
    }

    private func deliver(_ source: String, _ data: Data) {
        DispatchQueue.main.async { [weak self] in self?.onChunk?(source, data) }
    }

    // MARK: SCStreamOutput

    func stream(_ stream: SCStream, didOutputSampleBuffer sb: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, sb.isValid, let chunker = sysChunker,
              let desc = CMSampleBufferGetFormatDescription(sb) else { return }
        let fmt = AVAudioFormat(cmAudioFormatDescription: desc)
        let frames = AVAudioFrameCount(CMSampleBufferGetNumSamples(sb))
        guard frames > 0, let pcm = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: frames) else { return }
        pcm.frameLength = frames
        let status = CMSampleBufferCopyPCMDataIntoAudioBufferList(
            sb, at: 0, frameCount: Int32(frames), into: pcm.mutableAudioBufferList)
        if status == noErr { chunker.push(pcm) }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        // The system stream died (display change, permission revoked). The mic keeps going.
        self.stream = nil
    }
}
