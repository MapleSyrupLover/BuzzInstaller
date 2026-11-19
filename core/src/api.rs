//! FFI surface for `flutter_rust_bridge`.
//!
//! Only compiled with `--features bridge`, after
//! `flutter_rust_bridge_codegen generate` has produced `frb_generated.rs`.
//!
//! Keep this module thin: it adapts [`crate`] types to FFI-friendly ones and
//! nothing else. All logic lives in the sibling modules and is unit-tested
//! without the bridge.

use std::path::Path;

use crate::chunking::{self, ChunkSpec};
use crate::hash;
use crate::link;

/// FFI mirror of [`ChunkSpec`].
#[derive(Debug, Clone)]
pub struct FfiChunk {
    pub index: u64,
    pub offset: u64,
    pub size: u64,
    pub name: String,
    pub sha256: String,
}

impl From<ChunkSpec> for FfiChunk {
    fn from(c: ChunkSpec) -> Self {
        Self {
            index: c.index,
            offset: c.offset,
            size: c.size,
            name: c.name,
            sha256: c.sha256,
        }
    }
}

#[flutter_rust_bridge::frb(sync)]
pub fn bz_is_valid_code(code: String) -> bool {
    link::is_valid_code(&code)
}

#[flutter_rust_bridge::frb(sync)]
pub fn bz_extract_code(raw: String) -> Result<String, String> {
    link::extract_code(&raw).map_err(|e| e.to_string())
}

#[flutter_rust_bridge::frb(sync)]
pub fn bz_share_link(code: String) -> String {
    link::code_to_bz_url(&code)
}

#[flutter_rust_bridge::frb(sync)]
pub fn bz_download_url(code: String) -> String {
    link::code_to_url(&code)
}

#[flutter_rust_bridge::frb(sync)]
pub fn bz_chunk_count(file_size: u64, chunk_size: u64) -> u64 {
    chunking::chunk_count(file_size, chunk_size)
}

#[flutter_rust_bridge::frb(sync)]
pub fn bz_describe_chunk_plan(total_size: u64, chunk_size: u64) -> Vec<u64> {
    let (count, last) = chunking::describe_chunk_plan(total_size, chunk_size);
    vec![count, last]
}

#[flutter_rust_bridge::frb(sync)]
pub fn bz_chunk_name(
    original_name: String,
    transfer_uuid: String,
    index: u64,
    total: u64,
) -> String {
    chunking::chunk_name(&original_name, &transfer_uuid, index, total)
}

/// Plan chunks on the Rust side. Blocking — the Dart side must not await this
/// on the UI isolate (flutter_rust_bridge runs it on a worker pool).
pub fn bz_plan_chunks(
    path: String,
    chunk_size: u64,
    transfer_uuid: String,
    compute_hashes: bool,
) -> Result<Vec<FfiChunk>, String> {
    chunking::plan_chunks(Path::new(&path), chunk_size, &transfer_uuid, compute_hashes)
        .map(|specs| specs.into_iter().map(FfiChunk::from).collect())
        .map_err(|e| e.to_string())
}

pub fn bz_hash_file(path: String) -> Result<String, String> {
    hash::hash_file(Path::new(&path)).map_err(|e| e.to_string())
}

pub fn bz_hash_range(path: String, offset: u64, length: u64) -> Result<String, String> {
    hash::hash_range(Path::new(&path), offset, length).map_err(|e| e.to_string())
}
