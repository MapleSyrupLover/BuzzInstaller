//! Streaming SHA-256 over files and byte ranges.
//!
//! Ported from `hash_file` / `hash_range` in
//! `src/BuzzInstaller/core/chunking.py`. Memory stays bounded at
//! [`IO_BLOCK`](crate::chunking::IO_BLOCK) regardless of file size.

use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;

use sha2::{Digest, Sha256};

use crate::chunking::IO_BLOCK;
use crate::error::{CoreError, CoreResult};

/// SHA-256 of a byte range, streamed with bounded memory.
pub fn hash_range(path: &Path, offset: u64, length: u64) -> CoreResult<String> {
    let mut file = File::open(path)?;
    file.seek(SeekFrom::Start(offset))?;

    let mut digest = Sha256::new();
    let mut remaining = length;
    let mut buf = vec![0u8; IO_BLOCK];

    while remaining > 0 {
        let want = remaining.min(IO_BLOCK as u64) as usize;
        let read = file.read(&mut buf[..want])?;
        if read == 0 {
            return Err(CoreError::UnexpectedEof(offset));
        }
        digest.update(&buf[..read]);
        remaining -= read as u64;
    }

    Ok(hex(&digest.finalize()))
}

/// Streaming SHA-256 of an entire file.
pub fn hash_file(path: &Path) -> CoreResult<String> {
    let mut file = File::open(path)?;
    let mut digest = Sha256::new();
    let mut buf = vec![0u8; IO_BLOCK];

    loop {
        let read = file.read(&mut buf)?;
        if read == 0 {
            break;
        }
        digest.update(&buf[..read]);
    }

    Ok(hex(&digest.finalize()))
}

fn hex(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        out.push_str(&format!("{b:02x}"));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    const ABC_SHA256: &str = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";

    #[test]
    fn hashes_a_known_file() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("abc.txt");
        std::fs::File::create(&path)
            .unwrap()
            .write_all(b"abc")
            .unwrap();

        assert_eq!(hash_file(&path).unwrap(), ABC_SHA256);
        assert_eq!(hash_range(&path, 0, 3).unwrap(), ABC_SHA256);
    }

    #[test]
    fn hashes_a_sub_range() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("abcabc.txt");
        std::fs::File::create(&path)
            .unwrap()
            .write_all(b"abcabc")
            .unwrap();

        assert_eq!(hash_range(&path, 3, 3).unwrap(), ABC_SHA256);
    }

    #[test]
    fn reading_past_eof_is_an_error() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("short.txt");
        std::fs::File::create(&path)
            .unwrap()
            .write_all(b"abc")
            .unwrap();

        assert!(matches!(
            hash_range(&path, 0, 99),
            Err(CoreError::UnexpectedEof(0))
        ));
    }
}
