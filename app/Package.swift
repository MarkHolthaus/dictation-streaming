// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "Dictation",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(name: "Dictation", path: "Sources/Dictation")
    ]
)
