/// In-memory [BuzzCore] so the UI can be built before the Rust bridge exists.
///
/// This is a **test double**, not a second source of truth: the rules are a
/// direct transcription of `core/src/link.rs` and `core/src/chunking.rs`, and
/// they exist so widget tests and `--dart-define=BUZZ_CORE=fake` previews run
/// with no toolchain. Delete it once `RustBuzzCore` covers every page.
library;

import 'buzz_core.dart';

/// Mirrors `MIN_CHUNK_SIZE` / `DEFAULT_CHUNK_SIZE` in the Rust core.
const int kMinChunkSize = 1024 * 1024;
const int kDefaultChunkSize = 10 * 1024 * 1024 * 1024;

final RegExp _bareCode = RegExp(r'^[a-z0-9]{8,}');
final RegExp _scheme = RegExp(r'^bz://?(.+)$', caseSensitive: false);
final RegExp _bzUrl = RegExp(r'buzzheavier\.com/([a-z0-9]+)', caseSensitive: false);

class FakeBuzzCore implements BuzzCore {
  FakeBuzzCore({this.fileSizes = const {}});

  /// Optional `path -> size` map so `planChunks` can pretend files exist.
  final Map<String, int> fileSizes;

  @override
  bool isValidCode(String code) =>
      _bareCode.hasMatch(code.trim().toLowerCase());

  @override
  String extractCode(String raw) {
    var text = raw.trim();
    final scheme = _scheme.firstMatch(text);
    if (scheme != null) text = scheme.group(1)!;

    final url = _bzUrl.firstMatch(text);
    if (url != null) return url.group(1)!.toLowerCase();

    final bare = _bareCode.firstMatch(text.trim().toLowerCase());
    if (bare != null) return bare.group(0)!;

    throw BuzzCoreException('Could not parse a BZ code from "$raw".');
  }

  @override
  ChunkPlan describePlan(int totalBytes, int chunkSize) {
    if (totalBytes <= 0) return ChunkPlan.empty;
    final size = chunkSize.clamp(kMinChunkSize, 1 << 40);
    final count = (totalBytes / size).ceil();
    final last = totalBytes - (count - 1) * size;
    return ChunkPlan(count: count, lastChunkSize: last);
  }

  @override
  Future<List<ChunkSpec>> planChunks(
    String path, {
    int? chunkSize,
    String transferUuid = '',
    bool computeHashes = false,
  }) async {
    final total = fileSizes[path];
    if (total == null) {
      throw BuzzCoreException('Source file does not exist: $path');
    }
    final plan = describePlan(total, chunkSize ?? kDefaultChunkSize);
    return List<ChunkSpec>.generate(plan.count, (i) {
      final offset = i * (chunkSize ?? kDefaultChunkSize);
      final size = i == plan.count - 1 ? plan.lastChunkSize : (chunkSize ?? kDefaultChunkSize);
      return ChunkSpec(
        index: i,
        offset: offset,
        size: size,
        name: 'part$i.bzbchunk',
        sha256: computeHashes ? 'fake-sha-$i' : '',
      );
    });
  }

  @override
  Future<String> hashFile(String path) async =>
      throw const BuzzCoreException('FakeBuzzCore does not hash real files.');
}
