/// The single contract the Flutter UI talks to.
///
/// The UI never calls foreign functions directly. Two implementations exist:
///
///  * `RustBuzzCore`  — wraps the `flutter_rust_bridge` bindings generated from
///    `/core` (the real implementation).
///  * `FakeBuzzCore`  — in-memory stand-in so pages can be built and tested
///    before the Rust bridge is wired up (`fake_buzz_core.dart`).
///
/// Keeping the UI behind this interface is what makes the port reviewable: each
/// page can be finished and tested against `FakeBuzzCore` while the Rust side
/// lands in parallel.
library;

/// One deterministic byte range inside a source file.
class ChunkSpec {
  const ChunkSpec({
    required this.index,
    required this.offset,
    required this.size,
    required this.name,
    this.sha256 = '',
  });

  final int index;
  final int offset;
  final int size;
  final String name;
  final String sha256;

  int get end => offset + size;
}

/// Result of describing a chunk plan for display.
class ChunkPlan {
  const ChunkPlan({required this.count, required this.lastChunkSize});

  final int count;
  final int lastChunkSize;

  static const empty = ChunkPlan(count: 0, lastChunkSize: 0);
}

/// Any failure surfaced by the core. `message` is already user-presentable.
class BuzzCoreException implements Exception {
  const BuzzCoreException(this.message);

  final String message;

  @override
  String toString() => 'BuzzCoreException: $message';
}

/// Core operations the desktop UI needs.
///
/// Pure functions are synchronous; anything touching the filesystem or network
/// is `Future`-based so a Rust worker thread can service it.
abstract class BuzzCore {
  /// True when [code] looks like a BuzzHeavier code.
  bool isValidCode(String code);

  /// Extract a code from `BZ://...`, a buzzheavier URL, or a bare code.
  ///
  /// Throws [BuzzCoreException] when nothing parseable is found.
  String extractCode(String raw);

  /// `(chunk_count, last_chunk_size)` for the upload preview UI.
  ChunkPlan describePlan(int totalBytes, int chunkSize);

  /// Plan chunks for a file. Hashes are computed only when asked.
  Future<List<ChunkSpec>> planChunks(
    String path, {
    int? chunkSize,
    String transferUuid = '',
    bool computeHashes = false,
  });

  /// Streaming SHA-256 of a whole file.
  Future<String> hashFile(String path);
}
