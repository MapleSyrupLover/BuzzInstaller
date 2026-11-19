//! Chunk planning, ported from `src/BuzzInstaller/core/chunking.py`.
//!
//! Rules preserved exactly:
//!
//! * chunks are byte ranges of the original file — no duplicate files;
//! * the final chunk may be smaller;
//! * chunk 0 starts at 0 and each chunk starts where the previous ended;
//! * chunk order is deterministic;
//! * the source file is never modified.

use std::fs;
use std::path::Path;

use crate::error::{CoreError, CoreResult};
use crate::hash::hash_range;

/// 10 GiB default, matching the desktop app.
pub const DEFAULT_CHUNK_SIZE: u64 = 10 * 1024 * 1024 * 1024;
/// Smallest allowed chunk.
pub const MIN_CHUNK_SIZE: u64 = 1024 * 1024;
/// Largest allowed chunk.
pub const MAX_CHUNK_SIZE: u64 = 1024 * 1024 * 1024 * 1024;
/// Streaming block size for hashing/copying (independent of chunk size).
pub const IO_BLOCK: usize = 1024 * 1024;

/// One deterministic byte range inside a source file.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ChunkSpec {
    pub index: u64,
    pub offset: u64,
    pub size: u64,
    pub name: String,
    pub sha256: String,
}

impl ChunkSpec {
    pub fn end(&self) -> u64 {
        self.offset + self.size
    }
}

/// Clamp an arbitrary size into the supported range.
pub fn clamp_chunk_size(value: i64) -> u64 {
    if value <= 0 {
        DEFAULT_CHUNK_SIZE
    } else {
        (value as u64).clamp(MIN_CHUNK_SIZE, MAX_CHUNK_SIZE)
    }
}

/// Number of chunks for `file_size`. Zero-byte files have no chunks.
pub fn chunk_count(file_size: u64, chunk_size: u64) -> u64 {
    if file_size == 0 {
        return 0;
    }
    let size = chunk_size.clamp(MIN_CHUNK_SIZE, MAX_CHUNK_SIZE);
    file_size.div_ceil(size)
}

/// `(chunk_count, last_chunk_size)` for display.
pub fn describe_chunk_plan(total_size: u64, chunk_size: u64) -> (u64, u64) {
    if total_size == 0 {
        return (0, 0);
    }
    let size = chunk_size.clamp(MIN_CHUNK_SIZE, MAX_CHUNK_SIZE);
    let count = chunk_count(total_size, size);
    let last = total_size - (count - 1) * size;
    (count, last)
}

/// Sanitise a name for use as a Windows file name (mirrors `sanitize_filename`).
pub fn sanitize_filename(name: &str, max_len: usize) -> String {
    let cleaned: String = name
        .chars()
        .map(|c| {
            if c.is_alphanumeric() || " ._-()".contains(c) {
                c
            } else {
                '_'
            }
        })
        .collect();
    let trimmed = cleaned.trim_matches(|c| c == ' ' || c == '.' || c == '_');
    let trimmed = if trimmed.is_empty() { "unnamed" } else { trimmed };
    trimmed.chars().take(max_len).collect()
}

/// `Path::stem` equivalent: `a.tar.gz` -> `a.tar`, `.bashrc` -> `.bashrc`.
fn path_stem(name: &str) -> String {
    let base = name.rsplit(['\\', '/']).next().unwrap_or(name);
    match base.rsplit_once('.') {
        Some((stem, _ext)) if !stem.is_empty() => stem.to_string(),
        _ => base.to_string(),
    }
}

/// Deterministic remote filename for one chunk.
///
/// Zero-padded so lexical order equals numeric order in a file listing; the
/// short transfer id prevents collisions between transfers of the same file.
pub fn chunk_name(original_name: &str, transfer_uuid: &str, index: u64, total: u64) -> String {
    let base = original_name.rsplit(['\\', '/']).next().unwrap_or("");
    let base = if base.is_empty() { "bundle" } else { base };

    let stem = sanitize_filename(&path_stem(base), 80);

    let short_id: String = transfer_uuid
        .chars()
        .filter(|c| c.is_alphanumeric())
        .take(8)
        .collect();
    let short_id = if short_id.is_empty() {
        "00000000".to_string()
    } else {
        short_id
    };

    let width = std::cmp::max(5, total.saturating_sub(1).to_string().len());

    format!(
        "{stem}.{short_id}.part{index:0width$}-of-{total:0width$}.bzbchunk",
        width = width
    )
}

/// Plan the chunks of `path` without touching it.
///
/// When `compute_hashes` is set, each chunk is streamed once to fill `sha256`.
pub fn plan_chunks(
    path: &Path,
    chunk_size: u64,
    transfer_uuid: &str,
    compute_hashes: bool,
) -> CoreResult<Vec<ChunkSpec>> {
    if !path.is_file() {
        return Err(CoreError::NotFound(path.display().to_string()));
    }

    let size = fs::metadata(path)?.len();
    let size_chunk = chunk_size.clamp(MIN_CHUNK_SIZE, MAX_CHUNK_SIZE);
    let total = chunk_count(size, size_chunk);
    if total == 0 {
        return Ok(Vec::new());
    }

    let file_name = path
        .file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_else(|| "bundle".to_string());

    let mut specs = Vec::with_capacity(total as usize);
    for index in 0..total {
        let offset = index * size_chunk;
        let length = std::cmp::min(size_chunk, size - offset);
        specs.push(ChunkSpec {
            index,
            offset,
            size: length,
            name: chunk_name(&file_name, transfer_uuid, index, total),
            sha256: String::new(),
        });
    }

    if compute_hashes {
        for spec in &mut specs {
            spec.sha256 = hash_range(path, spec.offset, spec.size)?;
        }
    }

    Ok(specs)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    const MIB: u64 = 1024 * 1024;

    #[test]
    fn clamps_out_of_range_sizes() {
        assert_eq!(clamp_chunk_size(0), DEFAULT_CHUNK_SIZE);
        assert_eq!(clamp_chunk_size(-5), DEFAULT_CHUNK_SIZE);
        assert_eq!(clamp_chunk_size(1), MIN_CHUNK_SIZE);
        assert_eq!(clamp_chunk_size(i64::MAX), MAX_CHUNK_SIZE);
    }

    #[test]
    fn counts_and_describes_chunks() {
        // Sizes are clamped to the 1 MiB floor, so use realistic magnitudes.
        const TEN: u64 = 10 * MIB;
        assert_eq!(chunk_count(0, TEN), 0);
        assert_eq!(chunk_count(25 * MIB, TEN), 3);
        assert_eq!(chunk_count(30 * MIB, TEN), 3);
        assert_eq!(describe_chunk_plan(25 * MIB, TEN), (3, 5 * MIB));
        assert_eq!(describe_chunk_plan(30 * MIB, TEN), (3, TEN));
        assert_eq!(describe_chunk_plan(0, TEN), (0, 0));
        // Anything below the floor counts as one chunk.
        assert_eq!(chunk_count(25, 10), 1);
    }

    #[test]
    fn names_chunks_deterministically() {
        assert_eq!(
            chunk_name("holiday video.mkv", "abc123def456", 2, 12),
            "holiday video.abc123de.part00002-of-00012.bzbchunk"
        );
        assert_eq!(
            chunk_name("C:\\Users\\me\\a.tar.gz", "", 0, 1),
            "a.tar.00000000.part00000-of-00001.bzbchunk"
        );
    }

    #[test]
    fn sanitises_names() {
        assert_eq!(sanitize_filename("bad:name?.txt", 200), "bad_name_.txt");
        assert_eq!(sanitize_filename("...", 200), "unnamed");
        assert_eq!(sanitize_filename("ok", 1), "o");
    }

    #[test]
    fn plans_and_hashes_a_real_file() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("sample.bin");
        {
            let mut f = std::fs::File::create(&path).unwrap();
            f.write_all(&vec![7u8; (3 * MIB) as usize]).unwrap();
        }

        let plain = plan_chunks(&path, 2 * MIB, "t", false).unwrap();
        assert_eq!(plain.len(), 2);
        assert_eq!(plain[0].offset, 0);
        assert_eq!(plain[0].size, 2 * MIB);
        assert_eq!(plain[1].offset, 2 * MIB);
        assert_eq!(plain[1].size, MIB);
        assert!(plain[0].sha256.is_empty());

        let hashed = plan_chunks(&path, 2 * MIB, "t", true).unwrap();
        assert_eq!(hashed[0].sha256.len(), 64);
        assert_eq!(hashed[1].sha256.len(), 64);
        assert_ne!(hashed[0].sha256, hashed[1].sha256);
    }

    #[test]
    fn missing_file_is_an_error() {
        let err = plan_chunks(Path::new("C:\\definitely\\not\\here.bin"), 1, "t", false);
        assert!(err.is_err());
    }
}
