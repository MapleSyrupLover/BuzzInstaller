import 'config_service.dart';

/// Chunk planning, ported from `src/BuzzInstaller/core/chunking.py`.
///
/// Chunks are byte ranges of the original file. Each part is uploaded as its
/// own remote file with a deterministic `.bzbchunk` name so the receiver can
/// order and join the parts again.
const int defaultChunkSize = 10 * 1024 * 1024 * 1024;
const int minChunkSize = 1024 * 1024;
const int maxChunkSize = 1024 * 1024 * 1024 * 1024;

class ChunkPart {
  const ChunkPart({
    required this.index,
    required this.offset,
    required this.size,
    required this.name,
  });

  final int index;
  final int offset;
  final int size;
  final String name;

  int get end => offset + size;
}

class ChunkPlan {
  const ChunkPlan({
    required this.totalSize,
    required this.chunkSize,
    required this.parts,
  });

  final int totalSize;
  final int chunkSize;
  final List<ChunkPart> parts;

  int get count => parts.length;
  bool get isChunked => parts.length > 1;

  String describe() {
    if (parts.isEmpty) return 'Empty file — nothing to upload.';
    if (!isChunked) return 'Single upload · ${_sizeLabel(totalSize)}';
    final first = parts.first.size;
    final last = parts.last.size;
    final sameSize = first == last;
    return '${parts.length} chunks · '
        '${sameSize ? _sizeLabel(first) : '${_sizeLabel(first)} + ${_sizeLabel(last)}'} each';
  }

  static String _sizeLabel(int bytes) {
    if (bytes < 1024) return '$bytes B';
    if (bytes < 1024 * 1024) return '${(bytes / 1024).toStringAsFixed(0)} KiB';
    if (bytes < 1024 * 1024 * 1024) {
      return '${(bytes / (1024 * 1024)).toStringAsFixed(1)} MiB';
    }
    return '${(bytes / (1024 * 1024 * 1024)).toStringAsFixed(2)} GiB';
  }
}

int clampChunkSize(int value) {
  if (value <= 0) return defaultChunkSize;
  return value.clamp(minChunkSize, maxChunkSize).toInt();
}

int chunkSizeForCount(int fileSize, int count) {
  final safeCount = count < 1 ? 1 : count;
  if (fileSize <= 0) return defaultChunkSize;
  return clampChunkSize((fileSize + safeCount - 1) ~/ safeCount);
}

int chunkCount(int fileSize, int chunkSize) {
  if (fileSize <= 0) return 0;
  final size = clampChunkSize(chunkSize);
  return (fileSize + size - 1) ~/ size;
}

String sanitizeFilename(String name, {int maxLength = 120}) {
  final base = name.split(RegExp(r'[\\/]')).last.trim();
  final cleaned = base.replaceAll(RegExp(r'[<>:"/\\|?*\x00-\x1F]'), '_');
  final trimmed = cleaned.isEmpty ? 'bundle' : cleaned;
  if (trimmed.length <= maxLength) return trimmed;
  return trimmed.substring(0, maxLength);
}

/// True when [filename] looks like a chunk part produced by [chunkName].
final RegExp chunkNamePattern = RegExp(
  r'\.[A-Za-z0-9]{8}\.part(\d+)-of-(\d+)\.bzbchunk$',
  caseSensitive: false,
);

/// Unique, deterministic remote filename for one chunk.
///
/// The original extension is preserved so joining the parts again restores
/// the real filename: `movie.mp4` becomes
/// `movie.mp4.1a2b3c4d.part00001-of-00004.bzbchunk`.
String chunkName(String originalName, String transferId, int index, int total) {
  final base = sanitizeFilename(originalName, maxLength: 80);
  final rawId = transferId.replaceAll(RegExp('[^A-Za-z0-9]'), '');
  final shortId = rawId.length >= 8
      ? rawId.substring(0, 8)
      : rawId.padRight(8, '0');
  final width = total.toString().length < 5 ? 5 : total.toString().length;
  final partLabel = (index + 1).toString().padLeft(width, '0');
  final totalLabel = total.toString().padLeft(width, '0');
  return '$base.$shortId.part$partLabel-of-$totalLabel.bzbchunk';
}

/// Original filename encoded in a chunk name, or `null` when [filename] is
/// not one of our parts.
String? originalNameFromChunk(String filename) {
  final cleaned = sanitizeFilename(filename);
  if (!chunkNamePattern.hasMatch(cleaned)) return null;
  final stem = cleaned.replaceFirst(chunkNamePattern, '');
  return stem.isEmpty ? null : stem;
}

/// 1-based part number of a chunk name, or `null` when it is not a part.
int? chunkPartNumber(String filename) {
  final match = chunkNamePattern.firstMatch(sanitizeFilename(filename));
  if (match == null) return null;
  return int.tryParse(match.group(1)!);
}

/// Total number of parts encoded in a chunk name, or `null`.
int? chunkPartTotal(String filename) {
  final match = chunkNamePattern.firstMatch(sanitizeFilename(filename));
  if (match == null) return null;
  return int.tryParse(match.group(2)!);
}

/// Build a plan for *chunkSize* bytes per part.
ChunkPlan planChunks({
  required int totalSize,
  required String fileName,
  required String transferId,
  required int chunkSize,
}) {
  final size = clampChunkSize(chunkSize);
  final total = chunkCount(totalSize, size);
  if (total <= 1) {
    return ChunkPlan(
      totalSize: totalSize,
      chunkSize: size,
      parts: [
        ChunkPart(
          index: 0,
          offset: 0,
          size: totalSize,
          name: sanitizeFilename(fileName),
        ),
      ],
    );
  }
  final parts = <ChunkPart>[];
  for (var index = 0; index < total; index++) {
    final offset = index * size;
    final length = (totalSize - offset) < size ? totalSize - offset : size;
    parts.add(
      ChunkPart(
        index: index,
        offset: offset,
        size: length,
        name: chunkName(fileName, transferId, index, total),
      ),
    );
  }
  return ChunkPlan(totalSize: totalSize, chunkSize: size, parts: parts);
}

/// Resolve the plan from the user's chunk settings.
///
/// `overrideCount` comes from the "ask when uploading" dialog. Modes:
/// * `auto`   — single upload (desktop default is a 10 GiB chunk size)
/// * `ask`    — treated as auto here; the page asks before uploading
/// * `count`  — split into roughly N equal parts
/// * `custom` — split by a fixed byte size
ChunkPlan resolvePlan({
  required AppConfig config,
  required int totalSize,
  required String fileName,
  required String transferId,
  int? overrideCount,
  int? overrideSize,
}) {
  if (overrideCount != null) {
    final size = chunkSizeForCount(totalSize, overrideCount);
    return planChunks(
      totalSize: totalSize,
      fileName: fileName,
      transferId: transferId,
      chunkSize: size,
    );
  }
  if (overrideSize != null && overrideSize > 0) {
    return planChunks(
      totalSize: totalSize,
      fileName: fileName,
      transferId: transferId,
      chunkSize: overrideSize,
    );
  }
  switch (config.uploadChunkMode) {
    case 'count':
      return planChunks(
        totalSize: totalSize,
        fileName: fileName,
        transferId: transferId,
        chunkSize: chunkSizeForCount(totalSize, config.uploadChunkCount),
      );
    case 'custom':
      final custom = config.uploadChunkSizeBytes;
      return planChunks(
        totalSize: totalSize,
        fileName: fileName,
        transferId: transferId,
        chunkSize: custom > 0 ? custom : defaultChunkSize,
      );
    case 'ask':
    case 'auto':
    default:
      return planChunks(
        totalSize: totalSize,
        fileName: fileName,
        transferId: transferId,
        chunkSize: defaultChunkSize,
      );
  }
}
