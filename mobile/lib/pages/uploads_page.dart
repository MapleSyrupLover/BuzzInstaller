import 'dart:io';
import 'dart:math';

import 'package:file_selector/file_selector.dart';
import 'package:flutter/material.dart';
import 'package:share_plus/share_plus.dart';

import '../models/transfer_record.dart';
import '../services/api_client.dart';
import '../services/chunking.dart' as chunking;
import '../services/config_service.dart';
import '../services/history_service.dart';
import '../theme/app_theme.dart';
import '../utils/clipboard.dart';
import '../utils/format.dart';
import '../widgets/components.dart';
import '../widgets/transfer_tile.dart';

class _PickedFile {
  const _PickedFile({
    required this.path,
    required this.name,
    required this.size,
  });

  final String path;
  final String name;
  final int size;
}

class UploadsPage extends StatefulWidget {
  const UploadsPage({super.key, required this.history});

  final HistoryService history;

  @override
  State<UploadsPage> createState() => _UploadsPageState();
}

class _UploadsPageState extends State<UploadsPage> {
  final BuzzApi _api = BuzzApi();

  List<_PickedFile> _files = const [];
  List<UploadResult> _results = const [];
  bool _busy = false;
  bool _cancelRequested = false;
  String? _error;
  String _phase = '';
  double _progress = 0;
  int _bytesDone = 0;
  int _bytesTotal = 0;
  DateTime? _startedAt;

  int get _totalSize => _files.fold(0, (sum, file) => sum + file.size);

  double? get _speed {
    final started = _startedAt;
    if (started == null || _bytesDone <= 0) return null;
    final seconds = DateTime.now().difference(started).inMilliseconds / 1000;
    if (seconds < 0.5) return null;
    return _bytesDone / seconds;
  }

  String _newTransferId() {
    final random = Random();
    final stamp = DateTime.now().microsecondsSinceEpoch.toRadixString(16);
    final salt = random.nextInt(0xFFFFFF).toRadixString(16).padLeft(6, '0');
    return '$stamp$salt';
  }

  String _selectorValue(AppConfig config) {
    switch (config.uploadChunkMode) {
      case 'ask':
        return 'ask';
      case 'count':
        return switch (config.uploadChunkCount) {
          2 => 'preset2',
          4 => 'preset4',
          8 => 'preset8',
          _ => 'custom',
        };
      case 'custom':
        return 'custom';
      default:
        return 'auto';
    }
  }

  List<String> _planLines(AppConfig config) {
    if (_files.isEmpty) return const [];
    if (config.uploadChunkMode == 'ask') {
      return const ['You will choose the number of parts before each upload.'];
    }
    return [
      for (final file in _files)
        '${file.name} · ${formatBytes(file.size)} · '
            '${chunking.resolvePlan(config: config, totalSize: file.size, fileName: file.name, transferId: _newTransferId()).describe()}',
    ];
  }

  Future<void> _pickFiles() async {
    try {
      final picked = await openFiles();
      if (picked.isEmpty) return;
      final added = <_PickedFile>[];
      for (final file in picked) {
        final size = await File(file.path).length();
        added.add(
          _PickedFile(
            path: file.path,
            name: file.name.isEmpty ? file.path.split('/').last : file.name,
            size: size,
          ),
        );
      }
      if (!mounted) return;
      setState(() {
        _files = [..._files, ...added];
        _results = const [];
        _error = null;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() => _error = 'Could not open the file picker: $error');
    }
  }

  void _removeFile(int index) {
    setState(() {
      final next = [..._files]..removeAt(index);
      _files = next;
    });
  }

  Future<void> _changeChunkMode(AppConfig config, String? value) async {
    if (value == null) return;
    if (value == 'ask') {
      await config.set('upload_chunk_mode', 'ask');
    } else if (value == 'preset2') {
      await config.setMany({
        'upload_chunk_mode': 'count',
        'upload_chunk_count': 2,
      });
    } else if (value == 'preset4') {
      await config.setMany({
        'upload_chunk_mode': 'count',
        'upload_chunk_count': 4,
      });
    } else if (value == 'preset8') {
      await config.setMany({
        'upload_chunk_mode': 'count',
        'upload_chunk_count': 8,
      });
    } else if (value == 'custom') {
      await _editCustomChunking(config);
    } else {
      await config.set('upload_chunk_mode', 'auto');
    }
  }

  Future<void> _editCustomChunking(AppConfig config) async {
    final countController = TextEditingController(
      text: config.uploadChunkMode == 'count'
          ? '${config.uploadChunkCount}'
          : '4',
    );
    final sizeController = TextEditingController(
      text: config.uploadChunkSizeBytes > 0
          ? '${(config.uploadChunkSizeBytes / (1024 * 1024)).round()}'
          : '',
    );
    final saved = await showDialog<bool>(
      context: context,
      builder: (dialogContext) {
        return AlertDialog(
          title: const Text('Custom chunking'),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'Split each file into a number of parts, or use a fixed part '
                'size. Leave one field empty.',
                style: Theme.of(dialogContext).textTheme.bodySmall,
              ),
              const SizedBox(height: 16),
              TextField(
                controller: countController,
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(labelText: 'Number of parts'),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: sizeController,
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(
                  labelText: 'Part size (MiB)',
                  hintText: 'e.g. 100',
                ),
              ),
            ],
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(dialogContext, false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(dialogContext, true),
              child: const Text('Save'),
            ),
          ],
        );
      },
    );
    final sizeMb = int.tryParse(sizeController.text.trim()) ?? 0;
    final count = int.tryParse(countController.text.trim()) ?? 0;
    countController.dispose();
    sizeController.dispose();
    if (saved != true) return;
    if (sizeMb >= 1) {
      await config.setMany({
        'upload_chunk_mode': 'custom',
        'upload_chunk_size_bytes': sizeMb * 1024 * 1024,
      });
    } else if (count >= 2) {
      await config.setMany({
        'upload_chunk_mode': 'count',
        'upload_chunk_count': count.clamp(2, 512),
      });
    }
  }

  Future<int?> _askPartCount(AppConfig config) async {
    var count = config.uploadChunkCount.clamp(2, 512).toInt();
    final customController = TextEditingController(text: '$count');
    final choice = await showDialog<int>(
      context: context,
      builder: (dialogContext) {
        return StatefulBuilder(
          builder: (context, setDialogState) {
            return AlertDialog(
              title: const Text('How many parts?'),
              content: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Splitting a file uploads each part as its own link. '
                    'BuzzInstaller joins the parts again when downloading.',
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                  const SizedBox(height: 12),
                  Wrap(
                    spacing: 8,
                    children: [
                      for (final preset in const [1, 2, 4, 8])
                        ChoiceChip(
                          label: Text(preset == 1 ? 'Single' : '$preset'),
                          selected: count == preset,
                          onSelected: (_) =>
                              setDialogState(() => count = preset),
                        ),
                    ],
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: customController,
                    keyboardType: TextInputType.number,
                    decoration: const InputDecoration(
                      labelText: 'Custom number of parts',
                    ),
                    onChanged: (value) {
                      final parsed = int.tryParse(value);
                      if (parsed != null) {
                        setDialogState(() => count = parsed.clamp(1, 512).toInt());
                      }
                    },
                  ),
                ],
              ),
              actions: [
                TextButton(
                  onPressed: () => Navigator.pop(dialogContext),
                  child: const Text('Cancel'),
                ),
                FilledButton(
                  onPressed: () => Navigator.pop(dialogContext, count),
                  child: const Text('Upload'),
                ),
              ],
            );
          },
        );
      },
    );
    customController.dispose();
    return choice;
  }

  Future<bool> _confirmUpload(AppConfig config, int? overrideCount) async {
    final files = _files;
    final lines = files.map((file) {
      final plan = chunking.resolvePlan(
        config: config,
        totalSize: file.size,
        fileName: file.name,
        transferId: _newTransferId(),
        overrideCount: overrideCount,
      );
      return '${file.name} · ${formatBytes(file.size)} · ${plan.describe()}';
    }).toList(growable: false);
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text(
          files.length == 1 ? 'Upload this file?' : 'Upload ${files.length} files?',
        ),
        content: SizedBox(
          width: 380,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                '${formatBytes(_totalSize)} total',
                style: Theme.of(dialogContext).textTheme.titleMedium,
              ),
              const SizedBox(height: 10),
              for (final line in lines.take(6))
                Padding(
                  padding: const EdgeInsets.only(bottom: 4),
                  child: Text(
                    '• $line',
                    style: Theme.of(dialogContext).textTheme.bodySmall,
                  ),
                ),
              if (lines.length > 6)
                Text(
                  '+ ${lines.length - 6} more files',
                  style: Theme.of(dialogContext).textTheme.bodySmall,
                ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(dialogContext, true),
            child: const Text('Upload'),
          ),
        ],
      ),
    );
    return confirmed ?? false;
  }

  Future<void> _upload() async {
    if (_files.isEmpty || _busy) return;
    final config = AppConfigScope.of(context);

    int? overrideCount;
    if (config.uploadChunkMode == 'ask') {
      overrideCount = await _askPartCount(config);
      if (overrideCount == null) return;
    }

    if (config.boolValue('confirm_before_upload')) {
      final confirmed = await _confirmUpload(config, overrideCount);
      if (!confirmed) return;
    }

    final files = _files;
    setState(() {
      _busy = true;
      _cancelRequested = false;
      _error = null;
      _results = const [];
      _progress = 0;
      _bytesDone = 0;
      _bytesTotal = files.fold(0, (sum, file) => sum + file.size);
      _startedAt = DateTime.now();
      _phase = 'Preparing upload…';
    });

    final results = <UploadResult>[];
    try {
      for (final file in files) {
        if (_cancelRequested) throw const TransferCancelledException();
        final plan = chunking.resolvePlan(
          config: config,
          totalSize: file.size,
          fileName: file.name,
          transferId: _newTransferId(),
          overrideCount: overrideCount == 1 ? null : overrideCount,
        );
        final chunked = plan.isChunked;
        setState(() {
          _phase = chunked
              ? 'Uploading ${file.name} in ${plan.count} parts'
              : 'Uploading ${file.name}';
        });

        final parts = <UploadedPart>[];
        for (final part in plan.parts) {
          if (_cancelRequested) throw const TransferCancelledException();
          final uploaded = await _api.uploadStream(
            filename: chunked ? part.name : file.name,
            bytes: File(file.path).openRead(part.offset, part.end),
            length: part.size,
            accountId: config.accountId,
            isCancelled: () => _cancelRequested,
            onProgress: (sent, _) {
              if (!mounted) return;
              setState(() {
                _progress = (_bytesDone + sent) / max(1, _bytesTotal);
                if (chunked) {
                  _phase =
                      'Uploading ${file.name} · part ${part.index + 1} of ${plan.count}';
                }
              });
            },
          );
          parts.add(uploaded);
          _bytesDone += part.size;
        }

        final result = UploadResult(
          name: file.name,
          size: file.size,
          parts: parts,
        );
        results.add(result);
        await widget.history.add(
          TransferRecord(
            kind: 'upload',
            name: file.name,
            size: file.size,
            createdAt: DateTime.now(),
            code: result.firstCode,
            bzUrl: result.firstBzUrl,
            partCodes: result.codes.toList(growable: false),
          ),
        );
      }

      final links = results.expand((result) => result.bzUrls).toList();
      await copyLinks(links, config);
      if (!mounted) return;
      setState(() {
        _results = results;
        _files = const [];
        _busy = false;
        _progress = 1;
        _phase = 'Upload complete';
      });
      _snack(
        links.length == 1
            ? 'Upload complete — link copied to clipboard.'
            : 'Upload complete — ${links.length} links copied.',
      );
    } on TransferCancelledException {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = 'Upload cancelled.';
        _phase = '';
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = '$error';
        _phase = '';
      });
    } finally {
      if (mounted && _busy) setState(() => _busy = false);
    }
  }

  void _snack(String message) {
    if (!AppConfigScope.of(context).notificationsEnabled) return;
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(message)));
  }

  Future<void> _copyResult(UploadResult result) async {
    final config = AppConfigScope.of(context);
    await copyLinks(result.bzUrls.toList(), config);
    if (!mounted) return;
    _snack(result.isChunked ? 'All part links copied.' : 'Link copied.');
  }

  @override
  Widget build(BuildContext context) {
    final config = AppConfigScope.of(context);
    final theme = BuzzThemeData.of(context);
    final planLines = _planLines(config);

    return PageBody(
      children: [
        BuzzCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          'Upload to BuzzHeavier',
                          style: TextStyle(
                            fontSize: 17,
                            fontWeight: FontWeight.w700,
                            color: theme.palette.textPrimary,
                          ),
                        ),
                        const SizedBox(height: 4),
                        Text(
                          'Pick files and share the BZ link with anyone.',
                          style: TextStyle(
                            fontSize: 12.5,
                            color: theme.palette.textSecondary,
                          ),
                        ),
                      ],
                    ),
                  ),
                  if (_files.isNotEmpty)
                    StatusPill(
                      label: '${_files.length} selected',
                      icon: Icons.check_circle_outline,
                    ),
                ],
              ),
              const SizedBox(height: 16),
              OutlinedButton.icon(
                onPressed: _busy ? null : _pickFiles,
                icon: const Icon(Icons.add, size: 19),
                label: Text(_files.isEmpty ? 'Choose files' : 'Add more files'),
              ),
              if (_files.isNotEmpty) ...[
                const SizedBox(height: 8),
                for (var index = 0; index < _files.length; index++)
                  _FileRow(
                    file: _files[index],
                    onRemove: _busy ? null : () => _removeFile(index),
                  ),
              ],
            ],
          ),
        ),
        SectionCard(
          title: 'Chunking',
          subtitle:
              'Parts are uploaded as separate links. BuzzInstaller rejoins them on download.',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 12),
                decoration: BoxDecoration(
                  color: theme.palette.inputBg,
                  borderRadius: BorderRadius.circular(theme.radius),
                  border: Border.all(color: theme.palette.border),
                ),
                child: DropdownButtonHideUnderline(
                  child: DropdownButton<String>(
                    value: _selectorValue(config),
                    isExpanded: true,
                    borderRadius: BorderRadius.circular(theme.radius),
                    padding: const EdgeInsets.symmetric(vertical: 4),
                    items: const [
                      DropdownMenuItem(
                        value: 'auto',
                        child: Text('Automatic (single upload)'),
                      ),
                      DropdownMenuItem(
                        value: 'ask',
                        child: Text('Ask when uploading'),
                      ),
                      DropdownMenuItem(
                        value: 'preset2',
                        child: Text('2 parts'),
                      ),
                      DropdownMenuItem(
                        value: 'preset4',
                        child: Text('4 parts'),
                      ),
                      DropdownMenuItem(
                        value: 'preset8',
                        child: Text('8 parts'),
                      ),
                      DropdownMenuItem(
                        value: 'custom',
                        child: Text('Custom…'),
                      ),
                    ],
                    onChanged: _busy
                        ? null
                        : (value) => _changeChunkMode(config, value),
                  ),
                ),
              ),
              if (planLines.isNotEmpty) ...[
                const SizedBox(height: 10),
                for (final line in planLines)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 3),
                    child: Text(
                      line,
                      style: TextStyle(
                        fontSize: 12,
                        color: theme.palette.textSecondary,
                      ),
                    ),
                  ),
              ] else ...[
                const SizedBox(height: 10),
                Text(
                  'Add files to preview the chunk plan.',
                  style: TextStyle(
                    fontSize: 12,
                    color: theme.palette.textTertiary,
                  ),
                ),
              ],
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
                        '${formatBytes(_bytesDone)} of ${formatBytes(_bytesTotal)}'
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
            child: Row(
              children: [
                Icon(Icons.error_outline, color: theme.palette.error, size: 20),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    _error!,
                    style: TextStyle(fontSize: 12.5, color: theme.palette.error),
                  ),
                ),
                IconButton(
                  onPressed: () => setState(() => _error = null),
                  icon: const Icon(Icons.close, size: 18),
                ),
              ],
            ),
          ),
        if (!_busy && _files.isNotEmpty)
          FilledButton.icon(
            onPressed: _upload,
            icon: const Icon(Icons.cloud_upload_outlined, size: 20),
            label: Text(
              'Upload ${_files.length == 1 ? _files.first.name : '${_files.length} files'} · ${formatBytes(_totalSize)}',
            ),
          ),
        for (final result in _results) _ResultCard(
          result: result,
          onCopy: () => _copyResult(result),
          onShare: () => SharePlus.instance.share(
            ShareParams(text: result.bzUrls.join('\n')),
          ),
        ),
        AnimatedBuilder(
          animation: widget.history,
          builder: (context, _) {
            final uploads = widget.history.uploads.take(6).toList();
            return SectionCard(
              title: 'Recent uploads',
              subtitle: 'Links stay available while BuzzHeavier keeps the file.',
              trailing: uploads.isEmpty
                  ? null
                  : TextButton(
                      onPressed: () => widget.history.clear(uploads: true),
                      child: const Text('Clear'),
                    ),
              child: uploads.isEmpty
                  ? const EmptyState(
                      icon: Icons.history,
                      text: 'Uploaded files will show up here.',
                    )
                  : Column(
                      children: [
                        for (final record in uploads)
                          TransferTile(
                            record: record,
                            onCopy: () => copyLinks(
                              record.partCodes.isEmpty
                                  ? [record.bzUrl ?? '']
                                  : record.partCodes
                                        .map((code) => 'BZ://$code')
                                        .toList(),
                              config,
                            ),
                            onShare: () => SharePlus.instance.share(
                              ShareParams(
                                text: record.partCodes.isEmpty
                                    ? record.bzUrl ?? ''
                                    : record.partCodes
                                          .map((code) => 'BZ://$code')
                                          .join('\n'),
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

class _FileRow extends StatelessWidget {
  const _FileRow({required this.file, this.onRemove});

  final _PickedFile file;
  final VoidCallback? onRemove;

  @override
  Widget build(BuildContext context) {
    final theme = BuzzThemeData.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        children: [
          Icon(Icons.insert_drive_file_outlined,
              size: 19, color: theme.palette.textSecondary),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              file.name,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                fontSize: 13,
                color: theme.palette.textPrimary,
              ),
            ),
          ),
          const SizedBox(width: 8),
          Text(
            formatBytes(file.size),
            style: TextStyle(
              fontSize: 11.5,
              color: theme.palette.textTertiary,
            ),
          ),
          IconButton(
            onPressed: onRemove,
            icon: const Icon(Icons.close, size: 17),
            visualDensity: VisualDensity.compact,
            tooltip: 'Remove',
          ),
        ],
      ),
    );
  }
}

class _ResultCard extends StatelessWidget {
  const _ResultCard({
    required this.result,
    required this.onCopy,
    required this.onShare,
  });

  final UploadResult result;
  final VoidCallback onCopy;
  final VoidCallback onShare;

  @override
  Widget build(BuildContext context) {
    final theme = BuzzThemeData.of(context);
    return BuzzCard(
      borderColor: theme.palette.success,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Icon(Icons.check_circle, size: 20, color: theme.palette.success),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  result.isChunked
                      ? '${result.name} — ${result.parts.length} parts uploaded'
                      : '${result.name} uploaded',
                  style: TextStyle(
                    fontSize: 13.5,
                    fontWeight: FontWeight.w600,
                    color: theme.palette.textPrimary,
                  ),
                ),
              ),
              StatusPill(
                label: formatBytes(result.size),
                color: theme.palette.success,
              ),
            ],
          ),
          const SizedBox(height: 10),
          for (final part in result.parts)
            Padding(
              padding: const EdgeInsets.only(bottom: 4),
              child: SelectableText(
                part.bzUrl,
                style: TextStyle(
                  fontSize: 12.5,
                  color: theme.palette.accent,
                  fontWeight: FontWeight.w500,
                ),
              ),
            ),
          const SizedBox(height: 4),
          Row(
            children: [
              TextButton.icon(
                onPressed: onCopy,
                icon: const Icon(Icons.link, size: 17),
                label: Text(result.isChunked ? 'Copy all links' : 'Copy link'),
              ),
              const SizedBox(width: 4),
              TextButton.icon(
                onPressed: onShare,
                icon: const Icon(Icons.ios_share, size: 17),
                label: const Text('Share'),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
