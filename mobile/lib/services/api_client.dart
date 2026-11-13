import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;

import '../models/transfer_record.dart';

/// Base class for every BuzzHeavier failure shown to the user.
class BuzzApiException implements Exception {
  const BuzzApiException(this.message);

  final String message;

  @override
  String toString() => message;
}

class InvalidCodeException extends BuzzApiException {
  const InvalidCodeException(super.message);
}

class CloudflareBlockedException extends BuzzApiException {
  const CloudflareBlockedException(super.message);
}

class TransferCancelledException extends BuzzApiException {
  const TransferCancelledException() : super('Transfer cancelled.');
}

class NetworkException extends BuzzApiException {
  const NetworkException(super.message);
}

class DownloadResult {
  const DownloadResult({
    required this.file,
    required this.bytes,
    required this.filename,
  });

  final File file;
  final int bytes;
  final String filename;
}

class DownloadProgress {
  const DownloadProgress({required this.received, required this.total});

  final int received;
  final int total;

  double? get fraction => total > 0 ? received / total : null;
}

/// BuzzHeavier HTTP client.
///
/// Uploads go to `w.buzzheavier.com` with a raw `PUT`. Downloads resolve a
/// signed token from the public file page first, exactly like the desktop
/// app, because the token is required to fetch the file.
class BuzzApi {
  static const String baseUrl = 'https://buzzheavier.com';
  static const String uploadHost = 'https://w.buzzheavier.com';

  static const String userAgent =
      'Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/127.0.0.0 Mobile Safari/537.36';

  static const Map<String, String> _browserHeaders = {
    'User-Agent': userAgent,
    'Accept': 'text/html,application/xhtml+xml',
    'Accept-Language': 'en-US,en;q=0.9',
  };

  // -- Upload ---------------------------------------------------------
  Future<UploadedPart> uploadStream({
    required String filename,
    required Stream<List<int>> bytes,
    required int length,
    String? parentId,
    String? accountId,
    void Function(int sent, int total)? onProgress,
    bool Function()? isCancelled,
  }) async {
    final safeName = Uri.encodeComponent(filename);
    final directory = (parentId ?? '').trim();
    final url = Uri.parse(
      directory.isEmpty
          ? '$uploadHost/$safeName'
          : '$uploadHost/${Uri.encodeComponent(directory)}/$safeName',
    );

    final client = http.Client();
    final request = http.StreamedRequest('PUT', url);
    request.headers['User-Agent'] = userAgent;
    request.headers['Content-Type'] = 'application/octet-stream';
    request.contentLength = length;
    if (accountId != null && accountId.trim().isNotEmpty) {
      request.headers['Authorization'] = 'Bearer ${accountId.trim()}';
    }

    try {
      final responseFuture = client.send(request);
      var sent = 0;
      try {
        await for (final chunk in bytes) {
          if (isCancelled?.call() ?? false) {
            throw const TransferCancelledException();
          }
          request.sink.add(chunk);
          sent += chunk.length;
          onProgress?.call(sent, length);
        }
        await request.sink.close();
      } catch (error) {
        // Abort the in-flight request before propagating.
        try {
          await request.sink.close();
        } catch (_) {
          // Already closed by the transport.
        }
        rethrow;
      }

      final response = await responseFuture;
      final body = await response.stream.bytesToString();
      if (response.statusCode != 200 && response.statusCode != 201) {
        throw BuzzApiException(
          'Upload failed (HTTP ${response.statusCode}): '
          '${_shortDetail(body)}',
        );
      }

      final payload = _decodeJson(body);
      final data = payload is Map ? (payload['data'] ?? payload) : null;
      final code = data is Map ? (data['id'] ?? data['code']) : null;
      if (code == null || '$code'.isEmpty) {
        throw const BuzzApiException(
          'Could not read the file code from the upload response.',
        );
      }

      return UploadedPart(
        name: data is Map ? '${data['name'] ?? filename}' : filename,
        code: '$code',
        bzUrl: 'BZ://$code',
        size: data is Map
            ? (data['size'] as num?)?.toInt() ?? length
            : length,
      );
    } on SocketException catch (error) {
      throw NetworkException('Network error during upload: ${error.message}');
    } on http.ClientException catch (error) {
      throw NetworkException('Upload connection failed: ${error.message}');
    } finally {
      client.close();
    }
  }

  // -- Download -------------------------------------------------------
  Future<DownloadResult> downloadToFile({
    required String code,
    required File target,
    void Function(DownloadProgress progress)? onProgress,
    bool Function()? isCancelled,
  }) async {
    final client = HttpClient()..connectionTimeout = const Duration(seconds: 30);
    try {
      final pageUri = Uri.parse('$baseUrl/${Uri.encodeComponent(code)}');
      final pageRequest = await client.getUrl(pageUri);
      _browserHeaders.forEach(pageRequest.headers.set);
      final pageResponse = await pageRequest.close();
      final html = await pageResponse.transform(utf8.decoder).join();

      if (pageResponse.statusCode == 404) {
        throw InvalidCodeException('File not found (code: $code).');
      }
      if (pageResponse.statusCode == 403 || html.contains('Just a moment')) {
        throw const CloudflareBlockedException(
          'BuzzHeavier blocked this request with a Cloudflare challenge. '
          'Open the link in your browser instead.',
        );
      }
      if (pageResponse.statusCode != 200) {
        throw InvalidCodeException(
          'Could not open the file page (HTTP ${pageResponse.statusCode}).',
        );
      }

      final pattern = RegExp(
        '/+${RegExp.escape(code)}/download\\?t=([A-Za-z0-9._%\\-]+)',
      );
      final match = pattern.firstMatch(html);
      if (match == null) {
        throw InvalidCodeException(
          'The file is gone or its link expired (code: $code).',
        );
      }

      final downloadUri = Uri.parse(
        '$baseUrl/$code/download?t=${match.group(1)}',
      );
      final request = await client.getUrl(downloadUri);
      _browserHeaders.forEach(request.headers.set);
      request.headers.set(HttpHeaders.acceptHeader, '*/*');
      request.cookies.addAll(pageResponse.cookies);
      final response = await request.close();

      if (response.statusCode == 403) {
        throw const CloudflareBlockedException(
          'BuzzHeavier blocked the download. Open the link in your browser.',
        );
      }
      if (response.statusCode != 200) {
        throw BuzzApiException(
          'Download failed (HTTP ${response.statusCode}).',
        );
      }

      final total = response.contentLength;
      await target.parent.create(recursive: true);
      final sink = target.openWrite();
      var received = 0;
      try {
        await for (final chunk in response) {
          if (isCancelled?.call() ?? false) {
            throw const TransferCancelledException();
          }
          sink.add(chunk);
          received += chunk.length;
          onProgress?.call(
            DownloadProgress(received: received, total: total),
          );
        }
        await sink.flush();
        await sink.close();
      } catch (error) {
        try {
          await sink.close();
        } catch (_) {
          // Sink already closed.
        }
        try {
          if (await target.exists()) await target.delete();
        } catch (_) {
          // Best effort cleanup of the partial file.
        }
        rethrow;
      }

      return DownloadResult(
        file: target,
        bytes: received,
        filename: _filenameFromHeaders(response.headers, code),
      );
    } on SocketException catch (error) {
      throw NetworkException('Network error during download: ${error.message}');
    } finally {
      client.close(force: true);
    }
  }

  // -- Release info ---------------------------------------------------
  Future<ReleaseInfo> fetchLatestRelease() async {
    final client = http.Client();
    try {
      final response = await client.get(
        Uri.parse(
          'https://api.github.com/repos/MapleSyrupLover/BuzzInstaller/releases/latest',
        ),
        headers: {
          'User-Agent': 'BuzzInstaller-Mobile/1.0',
          'Accept': 'application/vnd.github+json',
        },
      );
      if (response.statusCode != 200) {
        throw BuzzApiException(
          'Update check failed (HTTP ${response.statusCode}).',
        );
      }
      final data = jsonDecode(response.body);
      if (data is! Map) {
        throw const BuzzApiException('Unexpected response from GitHub.');
      }
      return ReleaseInfo(
        tag: '${data['tag_name'] ?? ''}',
        name: '${data['name'] ?? ''}',
        url: '${data['html_url'] ?? ''}',
        body: '${data['body'] ?? ''}',
        publishedAt: DateTime.tryParse('${data['published_at'] ?? ''}'),
      );
    } finally {
      client.close();
    }
  }

  // -- Helpers --------------------------------------------------------
  static Object? _decodeJson(String body) {
    try {
      return jsonDecode(body);
    } catch (_) {
      return null;
    }
  }

  static String _shortDetail(String body) {
    final parsed = _decodeJson(body);
    if (parsed is Map) {
      final detail = parsed['msg'] ?? parsed['message'];
      if (detail != null && '$detail'.isNotEmpty) return '$detail';
    }
    final text = body.trim();
    if (text.isEmpty) return 'unknown error';
    return text.length > 200 ? '${text.substring(0, 200)}...' : text;
  }

  static String _filenameFromHeaders(HttpHeaders headers, String code) {
    final disposition = headers.value('content-disposition');
    if (disposition != null) {
      final extended = RegExp(
        r"filename\*=UTF-8''([^;]+)",
        caseSensitive: false,
      ).firstMatch(disposition);
      if (extended != null) {
        try {
          return Uri.decodeComponent(extended.group(1)!);
        } catch (_) {
          // Fall through to the plain filename.
        }
      }
      final plain = RegExp(
        r'filename="?([^";]+)"?',
        caseSensitive: false,
      ).firstMatch(disposition);
      if (plain != null) return plain.group(1)!.trim();
    }
    return 'buzzheavier-$code';
  }
}

class ReleaseInfo {
  const ReleaseInfo({
    required this.tag,
    required this.name,
    required this.url,
    required this.body,
    this.publishedAt,
  });

  final String tag;
  final String name;
  final String url;
  final String body;
  final DateTime? publishedAt;
}

/// Extract a BZ code from a `BZ://` code, a full URL or a bare code.
String? extractCode(String raw) {
  var text = raw.trim();
  if (text.isEmpty) return null;
  if (text.toLowerCase().startsWith('bz://')) {
    text = text.substring(5);
  } else if (text.contains('://')) {
    try {
      final uri = Uri.parse(text);
      final segments = uri.pathSegments
          .where((segment) => segment.isNotEmpty)
          .toList(growable: false);
      if (segments.isNotEmpty) text = segments.last;
    } catch (_) {
      // Keep the raw text and let validation decide.
    }
  }
  text = text.split('?').first.split('#').first;
  text = text.replaceAll(RegExp(r'/+$'), '').trim();
  if (!RegExp(r'^[A-Za-z0-9_-]{4,64}$').hasMatch(text)) return null;
  return text;
}

/// Extract every code from a pasted list of links (one per line or space).
List<String> extractCodes(String raw) {
  final codes = <String>[];
  for (final token in raw.split(RegExp(r'[\s,]+'))) {
    final code = extractCode(token);
    if (code != null && !codes.contains(code)) codes.add(code);
  }
  return codes;
}
