/// One completed upload or download, shown in the recent activity lists.
class TransferRecord {
  const TransferRecord({
    required this.kind,
    required this.name,
    required this.size,
    required this.createdAt,
    this.code,
    this.bzUrl,
    this.partCodes = const [],
    this.localPath,
    this.status = 'completed',
  });

  final String kind; // 'upload' | 'download'
  final String name;
  final int size;
  final DateTime createdAt;
  final String? code;
  final String? bzUrl;
  final List<String> partCodes;
  final String? localPath;
  final String status;

  bool get isUpload => kind == 'upload';

  Map<String, Object?> toJson() => {
    'kind': kind,
    'name': name,
    'size': size,
    'createdAt': createdAt.toIso8601String(),
    'code': code,
    'bzUrl': bzUrl,
    'partCodes': partCodes,
    'localPath': localPath,
    'status': status,
  };

  static TransferRecord? fromJson(Object? raw) {
    if (raw is! Map) return null;
    final createdAt = DateTime.tryParse('${raw['createdAt'] ?? ''}');
    return TransferRecord(
      kind: '${raw['kind'] ?? 'upload'}',
      name: '${raw['name'] ?? 'Unnamed'}',
      size: (raw['size'] as num?)?.toInt() ?? 0,
      createdAt: createdAt ?? DateTime.now(),
      code: raw['code'] as String?,
      bzUrl: raw['bzUrl'] as String?,
      partCodes: (raw['partCodes'] as List?)
              ?.map((value) => '$value')
              .toList(growable: false) ??
          const [],
      localPath: raw['localPath'] as String?,
      status: '${raw['status'] ?? 'completed'}',
    );
  }
}

/// All remote links belonging to one upload (single file or chunked).
class UploadResult {
  const UploadResult({
    required this.name,
    required this.size,
    required this.parts,
  });

  final String name;
  final int size;
  final List<UploadedPart> parts;

  bool get isChunked => parts.length > 1;
  String get firstCode => parts.isEmpty ? '' : parts.first.code;
  String get firstBzUrl => parts.isEmpty ? '' : parts.first.bzUrl;
  Iterable<String> get bzUrls => parts.map((part) => part.bzUrl);
  Iterable<String> get codes => parts.map((part) => part.code);
}

class UploadedPart {
  const UploadedPart({
    required this.name,
    required this.code,
    required this.bzUrl,
    required this.size,
  });

  final String name;
  final String code;
  final String bzUrl;
  final int size;
}
