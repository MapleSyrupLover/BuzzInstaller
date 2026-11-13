# BuzzInstaller Mobile

An Android companion to [BuzzInstaller](../README.md), built with Flutter.

It talks to the same BuzzHeavier endpoints as the desktop app, so a link
created on either device opens on the other.

## Features

- **Uploads** — pick one or more files and upload them to BuzzHeavier.
  Chunk plans are configurable: automatic, ask every time, quick presets
  (2 / 4 / 8 parts) or a custom number of parts. Each part's size is shown
  before uploading.
- **Downloads** — paste a `BZ://` code, a `buzzheavier.com` link, or a whole
  list of links. Part files (`.bzbchunk`) are joined back into the original
  file automatically.
- **Settings** — chunking defaults, BuzzHeavier account ID, clipboard
  lifetime, download options.
- **Customization** — light/dark/system theme, interface style presets,
  accent colour, corner radius and font scale, matching the desktop app.
- **Recent transfers** — recent uploads and downloads with copy/share/open
  actions.

## Development

```powershell
flutter pub get
flutter analyze
flutter build apk --debug
```

The app lives entirely in Dart under `lib/`; platform folders are generated
by `flutter create` and are intentionally unmodified apart from the app
name, icon and Android permissions.

### Known limitations (mobile)

- Android does not allow a background app to keep a transfer alive
  indefinitely. Transfers keep running while the app is open; the screen is
  kept awake during an active transfer.
- Downloads that Cloudflare blocks for non-browser clients can be opened in
  the system browser from the error card.
- Auto-extract of ZIP archives is not implemented on mobile yet; downloaded
  archives are saved intact.
