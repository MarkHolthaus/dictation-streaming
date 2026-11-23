import Foundation
import Network

/// Newline-delimited JSON over TCP to the Python engine. Everything runs on the main queue.
final class EngineClient {
    var onMessage: (([String: Any]) -> Void)?
    var onStateChange: (() -> Void)?

    private(set) var connected = false
    private(set) var models = ""
    private var conn: NWConnection?
    private var buffer = Data()
    private var reconnectScheduled = false
    private var pingTimer: Timer?
    private var pending: [String: (id: Int, done: ([String: Any]?) -> Void)] = [:]
    private var nextRequestId = 0

    func start() {
        connect()
        pingTimer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in
            self?.send(["type": "ping"])
        }
    }

    private func connect() {
        conn?.cancel()
        let c = NWConnection(host: "127.0.0.1", port: 47321, using: .tcp)
        conn = c
        buffer.removeAll()
        c.stateUpdateHandler = { [weak self, weak c] state in
            guard let self, let c, self.conn === c else { return }
            switch state {
            case .ready:
                self.setConnected(true)
                self.receive(c)
                self.send(["type": "ping"])
            case .failed, .cancelled, .waiting:
                self.setConnected(false)
                self.scheduleReconnect()
            default:
                break
            }
        }
        c.start(queue: .main)
    }

    private func setConnected(_ value: Bool) {
        if connected != value {
            connected = value
            if !value {
                models = ""
                failPending()
            }
            onStateChange?()
        }
    }

    private func scheduleReconnect() {
        guard !reconnectScheduled else { return }
        reconnectScheduled = true
        conn?.cancel()
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in
            guard let self else { return }
            self.reconnectScheduled = false
            self.connect()
        }
    }

    private func receive(_ c: NWConnection) {
        c.receive(minimumIncompleteLength: 1, maximumLength: 65536) { [weak self, weak c] data, _, isComplete, error in
            guard let self, let c, self.conn === c else { return }
            if let data, !data.isEmpty {
                self.buffer.append(data)
                self.drain()
            }
            if isComplete || error != nil {
                self.setConnected(false)
                self.scheduleReconnect()
            } else {
                self.receive(c)
            }
        }
    }

    private func drain() {
        while let nl = buffer.firstIndex(of: 0x0A) {
            let line = buffer.subdata(in: buffer.startIndex..<nl)
            buffer.removeSubrange(buffer.startIndex...nl)
            guard !line.isEmpty,
                  let obj = try? JSONSerialization.jsonObject(with: line) as? [String: Any] else { continue }
            if obj["type"] as? String == "pong" {
                if let m = obj["models"] as? [String: Any] {
                    let asr = m["asr"] as? String ?? "?"
                    let tagger = m["tagger"] as? String ?? "?"
                    models = "asr \(asr), tagger \(tagger)"
                    onStateChange?()
                }
            }
            if let type = obj["type"] as? String, let p = pending.removeValue(forKey: type) { p.done(obj) }
            onMessage?(obj)
        }
    }

    @discardableResult
    func send(_ message: [String: Any]) -> Bool {
        guard connected, let c = conn,
              var data = try? JSONSerialization.data(withJSONObject: message) else { return false }
        data.append(0x0A)
        c.send(content: data, completion: .contentProcessed { _ in })
        return true
    }

    /// Sends `msg` and returns the next reply with the same `type`. One in flight per type
    /// (a newer request makes the older one return nil). Nil on timeout or disconnect.
    func request(_ msg: [String: Any], timeout: TimeInterval = 5) async -> [String: Any]? {
        await withCheckedContinuation { cont in
            DispatchQueue.main.async { [self] in
                guard let type = msg["type"] as? String, send(msg) else { cont.resume(returning: nil); return }
                pending[type]?.done(nil)
                nextRequestId += 1
                let id = nextRequestId
                pending[type] = (id, { cont.resume(returning: $0) })
                DispatchQueue.main.asyncAfter(deadline: .now() + timeout) { [weak self] in
                    guard let p = self?.pending[type], p.id == id else { return }
                    self?.pending[type] = nil
                    p.done(nil)
                }
            }
        }
    }

    private func failPending() {
        let all = pending
        pending.removeAll()
        for p in all.values { p.done(nil) }
    }
}
