# Architecture: Flutter UI + Rust core

Status: **phase 0 (scaffold)**. The Python app in `src/` remains the shipping
product until the parity gates in §6 pass.

## 1. Decision

| Layer | Technology | Why |
|---|---|---|
| UI | **Flutter (Windows desktop)** | Custom-drawn UI is Flutter's strength; converges with `mobile/` |
| Core | **Rust** (`core/`) | Bounded-memory chunking, fast hashing, small binary, hard guarantees |
| Win32 integration | **Rust** (`windows` crate) | Registry, clipboard, locks, shortcuts are testable and FFI-light |
| Installer / updater | Python (unchanged, for now) | Separate executables; port last to keep scope sane |

Bridge: [`flutter_rust_bridge`](https://cjycode.com/flutter_rust_bridge/) v2.
It handles `Result`, structs, and long-running calls on a worker pool, so no
hand-written FFI marshalling.

## 2. Target layout

```
core/                     Rust crate "buzz_core" (no UI, no Flutter)
  src/link.rs             BZ:// parsing, validation, URL building   [done]
  src/chunking.rs         chunk planning, naming, sanitising        [done]
  src/hash.rs             streaming SHA-256                         [done]
  src/error.rs            one error type for the FFI boundary       [done]
  src/api.rs              #[frb] surface (feature = "bridge")       [done]
  src/frb_generated.rs    generated — never hand-edited
desktop/                  Flutter Windows app
  lib/core/buzz_core.dart interface the UI depends on               [done]
  lib/core/fake_buzz_core.dart  test double                        [done]
  lib/pages/…             ported from mobile/lib/pages
mobile/                   Android app (unchanged, shares Rust core later)
src/                      Python app (unchanged until cutover)
```

`core/` has no dependency on Flutter or Qt. It builds with `cargo test` and is
also usable from a future CLI (`bz`) without any UI.

## 3. Ownership of each subsystem

| Python today | Target | Notes |
|---|---|---|
| `core/chunking.py` | Rust | Done in phase 0 |
| `core/buzzheavier_api.py` (parsing part) | Rust | Done in phase 0 |
| `core/buzzheavier_api.py` (HTTP part) | Rust | Phase 2 — hardest piece |
| `core/bundle.py`, `bundle_service.py` | Rust | Phase 2; stream-based, no temp files |
| `core/transfer_db.py` | Rust (SQLite) | Phase 2 |
| `core/archive.py` | Rust (`zip`) | Phase 2 |
| `system/registry_manager.py` | Rust (`windows`) | Phase 4 |
| `system/context_menu.py` | Rust (`windows`) | Phase 4 — registry verbs only, no COM |
| `system/hotkey_service.py` | Dart plugin + Rust fallback | Phase 4; `RegisterHotKey` needs a message loop |
| `system/clipboard*.py` | Rust (`windows`) | Phase 4 |
| `system/transfer_lock.py` | Rust | Phase 4 |
| `system/start_menu.py`, `shortcuts.py` | Rust (COM `IShellLink`) | Phase 4 |
| `ui/**` (PySide6) | Flutter | Phase 3 |
| `main.py` (single instance, protocol dispatch) | Rust + Dart | Phase 4 |
| `BuzzInstallerInstaller`, `Setup`, `Updater` | Stay Python | Phase 6, optional |

## 4. FFI surface

Already defined in `core/src/api.rs`:

| Function | Kind | Purpose |
|---|---|---|
| `bz_is_valid_code` | sync | validation |
| `bz_extract_code` | sync | link parsing |
| `bz_share_link` | sync | `BZ://` clipboard link |
| `bz_download_url` | sync | `https://buzzheavier.com/...` |
| `bz_chunk_count` | sync | upload preview |
| `bz_describe_chunk_plan` | sync | upload preview |
| `bz_chunk_name` | sync | upload preview |
| `bz_plan_chunks` | async | filesystem read |
| `bz_hash_file` | async | filesystem read |
| `bz_hash_range` | async | verification |

Phase 2 adds **streams**, not new calls: `upload_stream(...) -> Stream<Progress>`
and `download_stream(...)`. `flutter_rust_bridge` supports `StreamSink`, which
is why it beats `dart:ffi` here.

Rule: **the Dart UI never does I/O for a transfer.** It issues a call and
renders events. That keeps the UI responsive and makes the core testable.

### Known divergences from Python

Verified by running the shipping implementation; see
[`core/tests/PARITY.md`](../core/tests/PARITY.md).

1. `BZ:code` (no slashes) and `bz:/code` now parse. Python's `BZ://?` regex
   requires a slash, so its own docstring example throws.
2. Uppercase codes (`BZ://H0L6LGM`) now parse; output is lowercased.

Everything else in phase 0 is byte-identical.

## 5. Win32 strategy

All OS integration lives in Rust behind a `platform` module, exposed as a few
coarse calls (`register_protocol(path)`, `set_clipboard(text)`,
`acquire_single_instance()`), not as Chatty per-field FFI.

Flutter plugins are only used where they touch the widget tree (tray icon,
window chrome, notifications). Everything else is Rust so it can be unit-tested
and reused by a future daemon.

Deliberate simplification: shell integration is **registry verbs**
(`Software\Classes\*\shell\BuzzInstallerSend`) and the hotkey is
`RegisterHotKey` — confirmed by reading `context_menu.py` and
`hotkey_service.py`. No COM shell extension, so this is a weekend of work, not
a month.

## 6. Phases and gates

| Phase | Work | Gate (don't proceed without it) | Agent-days |
|---|---|---|---|
| 0 | Scaffold, pure-logic parity | `cargo test` green; `flutter analyze` clean | done |
| 1 | Link + chunk + hash parity | Rust output equals Python for a fixture set | 1–2 |
| 2 | HTTP: upload, download, chunked bundles, resume, Cloudflare cookies | One real 85 MB upload and one download match Python byte-for-byte | 4–6 |
| 3 | UI pages on `BuzzCore` | Every page runs on `FakeBuzzCore`; `flutter test` green | 4–6 |
| 4 | Win32: protocol, context menu, clipboard, single instance, locks | `BZ://` activation end-to-end on a clean VM | 2–4 |
| 5 | Packaging, updater hand-off, code signing | Signed MSIX/EXE installs and updates on a clean VM | 3–5 |
| 6 | Cutover, keep Python as fallback for one release | Side-by-side QA on the same file set | 2–3 |

Total ≈ 15–26 agent-days, 3–5 weeks elapsed with a human verifying.

## 7. Testing strategy

1. **Rust unit tests** — already present for every pure function. They are the
   oracle; an agent iterating against `cargo test` converges fast.
2. **Golden fixtures** — dump Python's output for a fixed set of links, file
   sizes and names into `core/tests/fixtures/*.json`, then assert Rust matches.
   This is the single highest-value artifact for the port.
3. **Dart widget tests** against `FakeBuzzCore`.
4. **One manual VM checklist** per phase — registry, tray, protocol, updater.
   Batch them; context switching costs more than the tests.

## 8. Risks

| Risk | Mitigation |
|---|---|
| Silent behavioural drift in HTTP layer | Golden fixtures + real-file byte comparison before cutover |
| Cloudflare/cookie handling is finicky | Port `_LimitedReader`/`_StreamingUploadBody` last, against a live endpoint |
| Agent writes plausible-but-wrong Rust | `cargo clippy -D warnings`, no `unsafe` outside generated code |
| Two UIs diverge | Freeze desktop Python features during phases 3–5 apart from fixes |
| Flutter desktop plugin gaps | Cover Win32 in Rust, not in plugins |
| Scope creep into mobile | Mobile keeps working as-is; share the core only after phase 6 |

## 9. Definition of done

- [ ] `cargo test` and `cargo clippy` clean
- [ ] `flutter test` + `flutter analyze` clean
- [ ] `BZ://` link opens the app and downloads, on a clean VM
- [ ] 85 MB upload produces a link the Android app can open
- [ ] Multi-GB chunked bundle round-trips with correct hashes
- [ ] Alt+F1 hotkey and Explorer context menu work
- [ ] Installer, shortcut, PATH entry, and update all work
- [ ] Uninstall removes protocol + context menu entries
- [ ] Binary is < 30 MB and starts in < 1 s cold

## 10. Bootstrap (toolchain not yet installed)

```powershell
winget install Rustlang.Rustup
winget install --id Google.Flutter -e
cargo install flutter_rust_bridge_codegen

cd core; cargo test
cd ..\desktop; flutter create --platforms=windows .
flutter_rust_bridge_codegen integrate
flutter run -d windows
```

`flutter create` is required once: it generates the Windows runner, CMake
files, and plugin registrant that are impractical to hand-author.
