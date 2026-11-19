//! `BZ://` link parsing and validation.
//!
//! Mirrors `src/BuzzInstaller/core/buzzheavier_api.py` (`extract_code`,
//! `is_valid_code`) so links behave identically during the migration:
//!
//! ```text
//! BZ://h0l6lgmru052
//! bz:h0l6lgmru052
//! https://buzzheavier.com/h0l6lgmru052
//! h0l6lgmru052
//! ```
//!
//! One deliberate difference from the Python version, where `extract_code`
//! documents but does not implement two cases:
//!
//! * `BZ:h0l6lgmru052` (single or no slash) — Python's `BZ://?` requires a
//!   slash, so its own docstring example throws `InvalidCodeError`.
//! * `BZ://H0L6LGM` — Python validates only lowercase.
//!
//! Both are fixed here: the scheme regex accepts `bz:`, `bz:/` and `bz://`,
//! and the candidate is lowercased before validation.

use regex::Regex;
use std::sync::OnceLock;

use crate::error::CoreError;

/// Minimum length of a BuzzHeavier code (real codes look like `h0l6lgmru052`).
pub const MIN_CODE_LEN: usize = 8;

fn bare_code_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[a-z0-9]{8,}").expect("static regex is valid"))
}

fn scheme_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(?i)^bz:(?://)?(.+)$").expect("static regex is valid")
    })
}

fn bz_url_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)buzzheavier\.com/([a-z0-9]+)").expect("static regex is valid"))
}

/// True when `code` starts with at least [`MIN_CODE_LEN`] code characters.
///
/// Prefix match on purpose: this is a cheap heuristic, matching Python.
pub fn is_valid_code(code: &str) -> bool {
    let lowered = code.trim().to_ascii_lowercase();
    bare_code_re().is_match(&lowered)
}

/// Pull a BuzzHeavier code out of any supported link form.
pub fn extract_code(raw: &str) -> Result<String, CoreError> {
    let mut text = raw.trim().to_string();

    // Strip a `BZ://` / `BZ:` scheme, case-insensitively.
    if let Some(caps) = scheme_re().captures(&text) {
        if let Some(rest) = caps.get(1) {
            text = rest.as_str().to_string();
        }
    }

    // Full `buzzheavier.com/<code>` URL.
    if let Some(caps) = bz_url_re().captures(&text) {
        if let Some(code) = caps.get(1) {
            return Ok(code.as_str().to_ascii_lowercase());
        }
    }

    // Bare code.
    let lowered = text.trim().to_ascii_lowercase();
    if let Some(m) = bare_code_re().find(&lowered) {
        return Ok(m.as_str().to_string());
    }

    Err(CoreError::NotACode(raw.to_string()))
}

/// `BZ://<code>` for clipboard and sharing.
pub fn code_to_bz_url(code: &str) -> String {
    format!("BZ://{}", code)
}

/// Canonical download URL for a code.
pub fn code_to_url(code: &str) -> String {
    format!("https://buzzheavier.com/{}", code)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_all_link_forms() {
        for raw in [
            "BZ://h0l6lgmru052",
            "bz://h0l6lgmru052",
            "BZ:h0l6lgmru052",
            "bz:/h0l6lgmru052",
            "  BZ://h0l6lgmru052  ",
            "https://buzzheavier.com/h0l6lgmru052",
            "http://buzzheavier.com/h0l6lgmru052",
            "h0l6lgmru052",
        ] {
            assert_eq!(extract_code(raw).unwrap(), "h0l6lgmru052", "input: {raw}");
        }
    }

    #[test]
    fn uppercase_input_is_normalised() {
        // Intentional improvement over the Python implementation.
        assert_eq!(extract_code("BZ://H0L6LGM").unwrap(), "h0l6lgm");
        assert!(is_valid_code("H0L6LGM"));
    }

    #[test]
    fn rejects_non_codes() {
        assert!(extract_code("hello").is_err());
        assert!(extract_code("").is_err());
        assert!(extract_code("BZ://").is_err());
        assert!(!is_valid_code("short"));
    }

    #[test]
    fn builds_urls() {
        assert_eq!(code_to_bz_url("abc12345"), "BZ://abc12345");
        assert_eq!(code_to_url("abc12345"), "https://buzzheavier.com/abc12345");
    }
}
