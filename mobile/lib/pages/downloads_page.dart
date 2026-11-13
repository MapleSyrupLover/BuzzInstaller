import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:path_provider/path_provider.dart';
import 'package:share_plus/share_plus.dart';
import 'package:url_launcher/url_launcher.dart';

import '../models/transfer_record.dart';
import '../services/api_client.dart';
import '../services/chunking.dart'
    show
        chunkPartNumber,
        chunkPartTotal,
        originalNameFromChunk,
        sanitizeFilename;
import '../services/config_service.dart';
import '../services/history_service.dart';
import '../services/incoming_link.dart';
import '../theme/app_theme.dart';
import '../utils/clipboard.dart';
import '../utils/format.dart';
import '../widgets/components.dart';
import '../widgets/transfer_tile.dart';

class DownloadsPage extends StatefulWidget {
  const DownloadsPage({super.key, required this.history});

  final HistoryService history;

  @override
  State<DownloadsPage> createState() => _DownloadsPageState();
}

class _DownloadsPageState extends State<DownloadsPage> {
  final BuzzApi _api = BuzzApi();
  final TextEditingController _controller = TextEditingController();

  bool _busy = false;
  bool _cancelRequested = false;
  String? _error;
  String? _errorCode;
  String _phase = '';
  double _progress = 0;
  int _received = 0;
  int _totalBytes = 0;
  DateTime? _startedAt;
  List<TransferRecord> _lastDownloads = const [];
  Directory? _folder;

  @override
  void initState() {
    super.initState();
    IncomingLink.latest.addListener(_applyIncomingLink);
    _applyIncomingLink();
    _prepareFolder();
  }

  @override
  void dispose() {
    IncomingLink.latest.removeListener(_applyIncomingLink);
    _controller.dispose();
    super.dispose();
  }

  void _applyIncomingLink() {
    final link = IncomingLink.latest.value;
    if (link == null) return;
    _controller.text = link;
    if (mounted) setState(() {});
  }

  Future<void> _prepareFolder() async {
    try {
      final base = await getApplicationDocumentsDirectory();
      final folder = Directory('${base.path}/BuzzInstaller');
      await folder.create(recursive: true);
      if (!mounted) return;
      setState(() => _folder = folder);
    } catch (_) {
      // The folder is created lazily again when downloading.
    }
  }

  Future<void> _paste() async {
    final data = await Clipboard.getData(Clipboard.kTextPlain);
    final text = data?.text;
    if (text == null || text.trim().isEmpty) {
      if (!mounted) return;
      _snack('Clipboard is empty.');
      return;
    }
    _controller.text = text.trim();
    if (mounted) setState(() {});
  }

  double? get _speed {
    final started = _startedAt;
    if (started == null || _received <= 0) return null;
    final seconds = DateTime.now().difference(started).inMilliseconds / 1000;
    if (seconds < 0.5) return null;
    return _received / seconds;
  }

  Future<void> _download() async {
    if (_busy) return;
    final config = AppConfigScope.of(context);
    final raw = _controller.text.trim();
    final codes = extractCodes(raw);
    if (codes.isEmpty) {
      setState(() {
        _error = 'Paste a BZ link like BZ://h0l6lgmru052 or a '
            'buzzheavier.com link.';
        _errorCode = null;
      });
      return;
    }

    setState(() {
      _busy = true;
      _cancelRequested = false;
      _error = null;
      _errorCode = null;
      _progress = 0;
      _received = 0;
      _totalBytes = 0;
      _startedAt = DateTime.now();
      _phase = 'Preparing download…';
      _lastDownloads = const [];
    });

    Directory targetDir;
    try {
      final base = await getApplicationDocumentsDirectory();
      targetDir = Directory('${base.path}/BuzzInstaller');
      await targetDir.create(recursive: true);
      if (mounted) setState(() => _folder = targetDir);
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = 'Could not open the downloads folder: $error';
      });
      return;
    }

    final tempFiles = <File>[];
    final results = <DownloadResult>[];
    try {
      for (var index = 0; index < codes.length; index++) {
        if (_cancelRequested) throw const TransferCancelledException();
        final code = codes[index];
        setState(() {
          _phase = codes.length == 1
              ? 'Resolving $code…'
              : 'Downloading ${index + 1} of ${codes.length} · $code';
        });
        final temp = File(
          '${targetDir.path}/.bzpart-${index.toString().padLeft(3, '0')}-$code-${DateTime.now().microsecondsSinceEpoch}.tmp',
        );
        tempFiles.add(temp);
        final result = await _api.downloadToFile(
          code: code,
          target: temp,
          isCancelled: () => _cancelRequested,
          onProgress: (progress) {
            if (!mounted) return;
            setState(() {
              final perFile = progress.fraction ?? 0;
              _progress = (index + perFile) / codes.length;
              _received += 0;
              _phase = 'Downloading ${index + 1} of ${codes.length} · '
                  '${formatBytes(progress.received)}'
                  '${progress.total > 0 ? ' of ${formatBytes(progress.total)}' : ''}';
              if (index == 0 && progress.total > 0) {
                _totalBytes = progress.total;
              }
            });
          },
        );
        results.add(result);
      }

      final saved = await _finishDownloads(targetDir, codes, results, tempFiles);
      final records = <TransferRecord>[];
      for (final entry in saved) {
        final record = TransferRecord(
          kind: 'download',
          name: entry.name,
          size: entry.size,
          createdAt: DateTime.now(),
          code: entry.code,
          bzUrl: entry.code == null ? null : 'BZ://${entry.code}',
          localPath: entry.file.path,
        );
        records.add(record);
        await widget.history.add(record);
      }

      if (!mounted) return;
      setState(() {
        _busy = false;
        _progress = 1;
        _phase = 'Download complete';
        _lastDownloads = records;
      });
      _snack(
        saved.length == 1
            ? 'Saved ${saved.first.name}.'
            : 'Saved ${saved.length} files.',
      );
      if (config.openAfterDownload && saved.length == 1) {
        await SharePlus.instance.share(ShareParams(files: [XFile(saved.first.file.path)]));
      }
    } on TransferCancelledException {
      for (final temp in tempFiles) {
        try {
          if (await temp.exists()) await temp.delete();
        } catch (_) {
          // Best effort cleanup.
        }
      }
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = 'Download cancelled.';
        _phase = '';
      });
    } catch (error) {
      for (final temp in tempFiles) {
        try {
          if (await temp.exists()) await temp.delete();
        } catch (_) {
          // Best effort cleanup.
        }
      }
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = '$error';
        _errorCode = codes.first;
        _phase = '';
      });
    } finally {
      if (mounted && _busy) setState(() => _busy = false);
    }
  }

  Future<List<_SavedDownload>> _finishDownloads(
    Directory targetDir,
    List<String> codes,
    List<DownloadResult> results,
    List<File> tempFiles,
  ) async {
    final names = results
        .map((result) => originalNameFromChunk(result.filename))
        .toList(growable: false);
    final allChunks =
        results.length > 1 &&
        names.every((name) => name != null) &&
        names.toSet().length == 1;
    if (allChunks) {
      // Parts can be pasted in any order; join them in part order.
      final ordered = [...results]
        ..sort((a, b) {
          final left = chunkPartNumber(a.filename) ?? 0;
          final right = chunkPartNumber(b.filename) ?? 0;
          return left.compareTo(right);
        });
      final joinedName = names.first!;
      final expectedParts = chunkPartTotal(results.first.filename);
      if (expectedParts != null && expectedParts != ordered.length) {
        throw BuzzApiException(
          'This file needs $expectedParts parts but ${ordered.length} were '
          'given. Paste every part link and try again.',
        );
      }
      final target = await _uniqueFile(targetDir, joinedName);
      final sink = target.openWrite();
      var written = 0;
      try {
        for (final result in ordered) {
          await for (final chunk in result.file.openRead()) {
            sink.add(chunk);
            written += chunk.length;
          }
        }
        await sink.flush();
        await sink.close();
      } catch (_) {
        try {
          await sink.close();
        } catch (_) {
          // Already closed.
        }
        rethrow;
      }
      for (final temp in tempFiles) {
        try {
          if (await temp.exists()) await temp.delete();
        } catch (_) {
          // Best effort cleanup.
        }
      }
      return [
        _SavedDownload(
          file: target,
          name: joinedName,
          size: written,
          code: codes.length == 1 ? codes.first : null,
        ),
      ];
    }

    final saved = <_SavedDownload>[];
    for (var index = 0; index < results.length; index++) {
      final result = results[index];
      final baseName = _cleanName(result.filename);
      final target = await _uniqueFile(targetDir, baseName);
      try {
        await result.file.rename(target.path);
      } on FileSystemException {
        await result.file.copy(target.path);
        try {
          await result.file.delete();
        } catch (_) {
          // Best effort cleanup.
        }
      }
      saved.add(
        _SavedDownload(
          file: target,
          name: target.uri.pathSegments.last,
          size: result.bytes,
          code: codes[index],
        ),
      );
    }
    return saved;
  }

  String _cleanName(String raw) {
    final name = raw.trim().isEmpty ? 'buzzheavier-download' : raw.trim();
    final cleaned = sanitizeFilename(name);
    // A single pasted part link should still save with the real filename.
    return originalNameFromChunk(cleaned) ?? cleaned;
  }

  Future<File> _uniqueFile(Directory dir, String name) async {
    var candidate = File('${dir.path}/$name');
    if (!await candidate.exists()) return candidate;
    final dot = name.lastIndexOf('.');
    final stem = dot > 0 ? name.substring(0, dot) : name;
    final extension = dot > 0 ? name.substring(dot) : '';
    var index = 1;
    while (await candidate.exists()) {
      candidate = File('${dir.path}/$stem ($index)$extension');
      index++;
    }
    return candidate;
  }

  Future<void> _openInBrowser(String code) async {
    final uri = Uri.parse('https://buzzheavier.com/$code');
    await launchUrl(uri, mode: LaunchMode.externalApplication);
  }

  void _snack(String message) {
    if (!AppConfigScope.of(context).notificationsEnabled) return;
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(message)));
  }

  @override
  Widget build(BuildContext context) {
    final config = AppConfigScope.of(context);
    final theme = BuzzThemeData.of(context);

    return PageBody(
      children: [
        BuzzCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                'Download from BuzzHeavier',
                style: TextStyle(
                  fontSize: 17,
                  fontWeight: FontWeight.w700,
                  color: theme.palette.textPrimary,
                ),
              ),
              const SizedBox(height: 4),
              Text(
                'Paste one link, or several part links to join them. '
                'Files are saved inside the app’s Downloads folder.',
                style: TextStyle(
                  fontSize: 12.5,
                  height: 1.35,
                  color: theme.palette.textSecondary,
                ),
              ),
              const SizedBox(height: 14),
              TextField(
                controller: _controller,
                minLines: 2,
                maxLines: 5,
                keyboardType: TextInputType.multiline,
                enabled: !_busy,
                decoration: InputDecoration(
                  hintText: 'BZ://h0l6lgmru052',
                  suffixIcon: IconButton(
                    tooltip: 'Paste from clipboard',
                    onPressed: _busy ? null : _paste,
                    icon: const Icon(Icons.content_paste, size: 19),
                  ),
                ),
              ),
              const SizedBox(height: 12),
              FilledButton.icon(
                onPressed: _busy ? null : _download,
                icon: const Icon(Icons.download, size: 20),
                label: const Text('Download'),
              ),
            ],
          ),
        ),
        SectionCard(
          title: 'Save location',
          subtitle: _folder == null
              ? 'Preparing the downloads folder…'
              : _folder!.path,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Icon(
                    Icons.folder_outlined,
                    size: 18,
                    color: theme.palette.textSecondary,
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      'Android keeps these files inside BuzzInstaller storage. '
                      'Use Share on a finished download to move it anywhere.',
                      style: TextStyle(
                        fontSize: 12,
                        color: theme.palette.textSecondary,
                      ),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 6),
              SwitchListTile.adaptive(
                contentPadding: EdgeInsets.zero,
                title: const Text(
                  'Open after download',
                  style: TextStyle(fontSize: 13.5),
                ),
                subtitle: Text(
                  'Show the Android share sheet when a download finishes.',
                  style: TextStyle(
                    fontSize: 11.5,
                    color: theme.palette.textSecondary,
                  ),
                ),
                value: config.openAfterDownload,
                onChanged: (value) => config.set('open_after_download', value),
              ),
            ],
          ),
        ),
        if (_busy)
          BuzzCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(
                  _phase,
                  style: TextStyle(
                    fontSize: 13.5,
                    fontWeight: FontWeight.w600,
                    color: theme.palette.textPrimary,
                  ),
                ),
                const SizedBox(height: 12),
                ClipRRect(
                  borderRadius: BorderRadius.circular(999),
                  child: LinearProgressIndicator(value: _progress.clamp(0.0, 1.0).toDouble()),
                ),
                const SizedBox(height: 10),
                Row(
                  children: [
                    Text(
                      '${(_progress * 100).clamp(0, 100).toStringAsFixed(0)}%',
                      style: TextStyle(
                        fontSize: 12,
                        fontWeight: FontWeight.w600,
                        color: theme.palette.textSecondary,
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        '${formatBytes(_received)}'
                        '${_totalBytes > 0 ? ' of ${formatBytes(_totalBytes)}' : ''}'
                        '${_speed == null ? '' : ' · ${formatSpeed(_speed!)}'}',
                        style: TextStyle(
                          fontSize: 12,
                          color: theme.palette.textTertiary,
                        ),
                      ),
                    ),
                    TextButton(
                      onPressed: () => setState(() => _cancelRequested = true),
                      child: const Text('Cancel'),
                    ),
                  ],
                ),
              ],
            ),
          ),
        if (_error != null)
          BuzzCard(
            borderColor: theme.palette.error,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Icon(
                      Icons.error_outline,
                      color: theme.palette.error,
                      size: 20,
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(
                        _error!,
                        style: TextStyle(
                          fontSize: 12.5,
                          color: theme.palette.error,
                        ),
                      ),
                    ),
                    IconButton(
                      onPressed: () => setState(() => _error = null),
                      icon: const Icon(Icons.close, size: 18),
                    ),
                  ],
                ),
                if (_errorCode != null)
                  Align(
                    alignment: Alignment.centerLeft,
                    child: TextButton.icon(
                      onPressed: () => _openInBrowser(_errorCode!),
                      icon: const Icon(Icons.open_in_browser, size: 18),
                      label: const Text('Open in browser'),
                    ),
                  ),
              ],
            ),
          ),
        for (final record in _lastDownloads)
          BuzzCard(
            borderColor: theme.palette.success,
            child: Row(
              children: [
                Icon(
                  Icons.check_circle,
                  size: 20,
                  color: theme.palette.success,
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        record.name,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                          fontSize: 13.5,
                          fontWeight: FontWeight.w600,
                          color: theme.palette.textPrimary,
                        ),
                      ),
                      const SizedBox(height: 2),
                      Text(
                        '${formatBytes(record.size)} · saved',
                        style: TextStyle(
                          fontSize: 11.5,
                          color: theme.palette.textSecondary,
                        ),
                      ),
                    ],
                  ),
                ),
                IconButton(
                  tooltip: 'Share',
                  onPressed: record.localPath == null
                      ? null
                      : () => SharePlus.instance.share(ShareParams(files: [XFile(record.localPath!)])),
                  icon: const Icon(Icons.ios_share, size: 19),
                ),
              ],
            ),
          ),
        AnimatedBuilder(
          animation: widget.history,
          builder: (context, _) {
            final downloads = widget.history.downloads.take(6).toList();
            return SectionCard(
              title: 'Recent downloads',
              subtitle: 'Saved files stay on this device.',
              trailing: downloads.isEmpty
                  ? null
                  : TextButton(
                      onPressed: () => widget.history.clear(uploads: false),
                      child: const Text('Clear'),
                    ),
              child: downloads.isEmpty
                  ? const EmptyState(
                      icon: Icons.history,
                      text: 'Downloaded files will show up here.',
                    )
                  : Column(
                      children: [
                        for (final record in downloads)
                          TransferTile(
                            record: record,
                            onCopy: record.partCodes.isEmpty && record.code == null
                                ? null
                                : () => copyLinks([
                                    record.bzUrl ?? 'BZ://${record.code}',
                                  ], config),
                            onShare: record.localPath == null
                                ? null
                                : () => SharePlus.instance.share(
                                    ShareParams(
                                      files: [XFile(record.localPath!)],
                                    ),
                                  ),
                          ),
                      ],
                    ),
            );
          },
        ),
      ],
    );
  }
}

class _SavedDownload {
  const _SavedDownload({
    required this.file,
    required this.name,
    required this.size,
    this.code,
  });

  final File file;
  final String name;
  final int size;
  final String? code;
}
