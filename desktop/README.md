# BuzzInstaller Desktop (Flutter + Rust)

The new desktop client: Flutter for the UI, Rust for the core (`../core`).

**Status: phase 0 scaffold.** The Python app in `../src` is still the shipping
product. See [../docs/ARCHITECTURE_FLUTTER_RUST.md](../docs/ARCHITECTURE_FLUTTER_RUST.md).

## Why the folder is almost empty

A Flutter Windows app needs a generated runner (`windows/`, CMake, plugin
registrant). Hand-authoring it is fragile, so it is created once with
`flutter create` on a machine with the toolchain installed, then committed.

## Bootstrap

```powershell
# 1. toolchains
winget install Rustlang.Rustup
winget install --id Google.Flutter -e
cargo install flutter_rust_bridge_codegen

# 2. verify the core first (no Flutter needed)
cd ..\core
cargo test

# 3. generate the Windows runner here
cd ..\desktop
flutter create --platforms=windows .

# 4. wire the bridge (writes frb_generated.rs / frb_generated.dart)
flutter_rust_bridge_codegen integrate

# 5. run
flutter run -d windows
```

## Layout

```
lib/
  core/
    buzz_core.dart        contract the UI depends on   (written by hand)
    fake_buzz_core.dart   test double, no Rust needed  (written by hand)
    rust_buzz_core.dart   adapter over generated code  (add after integrate)
  pages/                  ported from ../../mobile/lib/pages
  widgets/                ported from ../../mobile/lib/widgets
  theme/                  ported from ../../mobile/lib/theme
```

Rebuild mobile UI from `mobile/` rather than from `src/ui`: the Dart code already
matches the visual design and the theming model.

## Rules

- UI never performs transfer I/O. It calls `BuzzCore` and renders events.
- No `dart:ffi` calls outside `lib/core/`.
- `lib/core/buzz_core.dart` is the seam: keep it small and keep it stable.
- Attribution terms apply to this directory too — see `../ATTRIBUTION.md`.
