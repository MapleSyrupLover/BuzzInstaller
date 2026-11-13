import 'package:buzzinstaller_mobile/services/api_client.dart';
import 'package:buzzinstaller_mobile/services/chunking.dart';
import 'package:buzzinstaller_mobile/utils/format.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('extractCode', () {
    test('accepts BZ:// links', () {
      expect(extractCode('BZ://h0l6lgmru052'), 'h0l6lgmru052');
      expect(extractCode('bz://h0l6lgmru052'), 'h0l6lgmru052');
    });

    test('accepts full URLs', () {
      expect(
        extractCode('https://buzzheavier.com/h0l6lgmru052'),
        'h0l6lgmru052',
      );
      expect(
        extractCode('https://buzzheavier.com/h0l6lgmru052/'),
        'h0l6lgmru052',
      );
    });

    test('accepts bare codes and rejects junk', () {
      expect(extractCode(' h0l6lgmru052 '), 'h0l6lgmru052');
      expect(extractCode('not a code!'), isNull);
      expect(extractCode(''), isNull);
    });
  });

  test('extractCodes de-duplicates pasted lists', () {
    final codes = extractCodes(
      'BZ://abc123\nhttps://buzzheavier.com/def456,abc123',
    );
    expect(codes, ['abc123', 'def456']);
  });

  group('chunk planning', () {
    test('single chunk for files that fit', () {
      final plan = planChunks(
        totalSize: 5 * 1024 * 1024,
        fileName: 'movie.mp4',
        transferId: 'deadbeefcafe',
        chunkSize: defaultChunkSize,
      );
      expect(plan.count, 1);
      expect(plan.isChunked, isFalse);
      expect(plan.parts.single.name, 'movie.mp4');
      expect(plan.parts.single.size, 5 * 1024 * 1024);
    });

    test('splits a file into the requested number of parts', () {
      final size = 10 * 1024 * 1024;
      final plan = planChunks(
        totalSize: size,
        fileName: 'clip.mov',
        transferId: 'deadbeefcafe',
        chunkSize: chunkSizeForCount(size, 4),
      );
      expect(plan.count, 4);
      expect(plan.parts.first.offset, 0);
      expect(
        plan.parts.fold<int>(0, (sum, part) => sum + part.size),
        size,
      );
      for (final part in plan.parts) {
        expect(part.name, contains('deadbeef'));
        expect(part.name, endsWith('.bzbchunk'));
      }
      expect(plan.parts[1].offset, plan.parts[0].size);
    });

    test('chunk names keep the original extension and can be decoded', () {
      final size = 4 * 1024 * 1024;
      final plan = planChunks(
        totalSize: size,
        fileName: 'holiday video.mp4',
        transferId: 'deadbeefcafe',
        chunkSize: chunkSizeForCount(size, 2),
      );
      expect(plan.count, 2);
      for (final part in plan.parts) {
        expect(part.name, startsWith('holiday video.mp4.'));
        expect(originalNameFromChunk(part.name), 'holiday video.mp4');
      }
      expect(chunkPartNumber(plan.parts.first.name), 1);
      expect(chunkPartNumber(plan.parts.last.name), 2);
      expect(chunkPartTotal(plan.parts.first.name), 2);
    });

    test('names without an extension still round-trip', () {
      final name = chunkName('LICENSE', 'deadbeefcafe', 0, 3);
      expect(name, startsWith('LICENSE.'));
      expect(originalNameFromChunk(name), 'LICENSE');
      expect(chunkPartNumber(name), 1);
      expect(chunkPartTotal(name), 3);
    });

    test('ordinary files are not treated as chunk parts', () {
      expect(originalNameFromChunk('movie.mp4'), isNull);
      expect(originalNameFromChunk('report.part1-of-2.bzbchunk'), isNull);
      expect(chunkPartNumber('movie.mp4'), isNull);
    });

    test('last chunk may be smaller', () {
      final size = 10 * 1024 * 1024 + 3;
      final plan = planChunks(
        totalSize: size,
        fileName: 'odd.bin',
        transferId: 'cafebabe1234',
        chunkSize: chunkSizeForCount(size, 3),
      );
      expect(plan.count, 3);
      expect(
        plan.parts.fold<int>(0, (sum, part) => sum + part.size),
        size,
      );
      expect(plan.parts.last.size, lessThanOrEqualTo(plan.parts.first.size));
    });

    test('clamps chunk sizes below one MiB', () {
      expect(clampChunkSize(1), minChunkSize);
      expect(clampChunkSize(0), defaultChunkSize);
    });

    test('formats sizes for the plan preview', () {
      final plan = planChunks(
        totalSize: 2 * 1024 * 1024,
        fileName: 'a.bin',
        transferId: '1234567890ab',
        chunkSize: chunkSizeForCount(2 * 1024 * 1024, 2),
      );
      expect(plan.describe(), contains('2 chunks'));
    });
  });

  test('formatBytes uses binary units', () {
    expect(formatBytes(512), '512 B');
    expect(formatBytes(1024), '1.0 KiB');
    expect(formatBytes(1024 * 1024), '1.0 MiB');
  });
}
