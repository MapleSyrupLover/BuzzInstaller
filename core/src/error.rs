//! One error type for the whole core, so the FFI boundary only ever has to
//! marshal a string.

use thiserror::Error;

pub type CoreResult<T> = Result<T, CoreError>;

#[derive(Debug, Error)]
pub enum CoreError {
    /// No BuzzHeavier code could be found in the input.
    #[error("could not parse a BZ code from {0:?}")]
    NotACode(String),

    /// The requested source file is missing.
    #[error("source file does not exist: {0}")]
    NotFound(String),

    /// Reading a byte range hit EOF earlier than expected.
    #[error("unexpected end of file at offset {0}")]
    UnexpectedEof(u64),

    /// A caller passed a negative length/offset that was clamped upstream.
    #[error("length cannot be negative")]
    NegativeLength,

    /// Any underlying I/O failure.
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
}
