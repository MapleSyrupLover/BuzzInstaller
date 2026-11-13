String formatBytes(num bytes) {
  final value = bytes < 0 ? 0 : bytes.toDouble();
  if (value < 1024) return '${value.toStringAsFixed(0)} B';
  if (value < 1024 * 1024) return '${(value / 1024).toStringAsFixed(1)} KiB';
  if (value < 1024 * 1024 * 1024) {
    return '${(value / (1024 * 1024)).toStringAsFixed(1)} MiB';
  }
  if (value < 1024 * 1024 * 1024 * 1024) {
    return '${(value / (1024 * 1024 * 1024)).toStringAsFixed(2)} GiB';
  }
  return '${(value / (1024 * 1024 * 1024 * 1024)).toStringAsFixed(2)} TiB';
}

String formatSpeed(double bytesPerSecond) =>
    '${formatBytes(bytesPerSecond)}/s';

/// A short, human friendly elapsed time such as `1m 04s`.
String formatDuration(Duration duration) {
  final seconds = duration.inSeconds;
  if (seconds < 60) return '${seconds}s';
  final minutes = seconds ~/ 60;
  final rest = seconds % 60;
  if (minutes < 60) return '${minutes}m ${rest.toString().padLeft(2, '0')}s';
  final hours = minutes ~/ 60;
  return '${hours}h ${(minutes % 60).toString().padLeft(2, '0')}m';
}

/// `Chunk 3 of 12` style label (1-based for display).
String humanChunkLabel(int index, int total) => 'Chunk ${index + 1} of $total';

String trimMiddle(String text, [int max = 42]) {
  if (text.length <= max) return text;
  final keep = (max - 3) ~/ 2;
  return '${text.substring(0, keep)}...${text.substring(text.length - keep)}';
}

/// `just now`, `4m ago`, `2h ago`, `3d ago`.
String relativeTime(DateTime time) {
  final diff = DateTime.now().difference(time);
  if (diff.inSeconds < 45) return 'just now';
  if (diff.inMinutes < 60) return '${diff.inMinutes}m ago';
  if (diff.inHours < 24) return '${diff.inHours}h ago';
  if (diff.inDays < 30) return '${diff.inDays}d ago';
  final months = diff.inDays ~/ 30;
  if (months < 12) return '${months}mo ago';
  return '${diff.inDays ~/ 365}y ago';
}
