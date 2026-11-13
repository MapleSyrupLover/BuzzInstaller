import 'package:flutter/material.dart';

import '../models/transfer_record.dart';
import '../theme/app_theme.dart';
import '../utils/format.dart';

/// One row in a recent-transfers list.
class TransferTile extends StatelessWidget {
  const TransferTile({
    super.key,
    required this.record,
    this.onCopy,
    this.onShare,
    this.onOpen,
  });

  final TransferRecord record;
  final VoidCallback? onCopy;
  final VoidCallback? onShare;
  final VoidCallback? onOpen;

  @override
  Widget build(BuildContext context) {
    final theme = BuzzThemeData.of(context);
    final palette = theme.palette;
    final partCount = record.partCodes.length;
    final subtitleParts = <String>[
      formatBytes(record.size),
      relativeTime(record.createdAt),
      if (partCount > 1) '$partCount parts',
    ];

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        children: [
          Container(
            width: 36,
            height: 36,
            decoration: BoxDecoration(
              color: palette.surfaceAlt,
              borderRadius: BorderRadius.circular(theme.radius),
            ),
            child: Icon(
              record.isUpload
                  ? Icons.arrow_upward_rounded
                  : Icons.arrow_downward_rounded,
              size: 18,
              color: record.isUpload ? palette.success : palette.accent,
            ),
          ),
          const SizedBox(width: 12),
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
                    color: palette.textPrimary,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  subtitleParts.join(' · '),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(fontSize: 11.5, color: palette.textSecondary),
                ),
              ],
            ),
          ),
          if (onOpen != null)
            IconButton(
              tooltip: 'Open',
              onPressed: onOpen,
              icon: const Icon(Icons.open_in_new, size: 19),
              visualDensity: VisualDensity.compact,
            ),
          if (onCopy != null)
            IconButton(
              tooltip: 'Copy link',
              onPressed: onCopy,
              icon: const Icon(Icons.link, size: 19),
              visualDensity: VisualDensity.compact,
            ),
          if (onShare != null)
            IconButton(
              tooltip: 'Share',
              onPressed: onShare,
              icon: const Icon(Icons.ios_share, size: 19),
              visualDensity: VisualDensity.compact,
            ),
        ],
      ),
    );
  }
}
