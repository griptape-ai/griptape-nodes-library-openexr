# Changelog

All notable changes to the OpenEXR Library will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/2.0.0/), and each version is
the `library_version` in `griptape-nodes-library.json`.
**Breaking** marks a change that can stop a saved workflow from working without edits.

## [Unreleased]

<!-- Entries go in changelog.d/, one file each. See changelog.d/README.md. -->

## [0.3.0] - 2026-11-04

### Added

- `Read EXR`, `Display EXR Part` and `Display EXR Channel` have a new `embed_color_profile`
  parameter (off by default). When on, the written PNG carries an ICC profile naming the `basic`
  display transform used, so colour-managed applications like Photoshop or a browser convert it
  correctly instead of reading every transform as sRGB. `EXR Sequence to Preview` instead always
  tags its video output's colour transfer characteristics, since an MP4 cannot carry an ICC profile.
- EXR files now get an automatically generated preview thumbnail wherever the platform shows
  artifact previews (such as the file browser), not only through nodes like `Display EXR Part`
  that render their own image.
  [#11](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/11)
- Added `Load EXR Sequence`, which takes a `Sequence` and outputs an ordered list of
  `EXRPartArtifact` descriptors for every frame's header, without reading pixel data. It connects
  directly into a `ForEach` loop for per-frame work such as `Display EXR Part`, replacing the
  `ScanSequence -> ForEach -> LoadEXR` manual composition.
  [#40](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/40)
- Added `Read EXR`, a single node that loads a path or `Sequence` and emits a viewable image
  alongside the typed `EXRPartArtifact` and `EXRChannelArtifact` descriptors, replacing the
  `Load EXR -> Get From List -> Display EXR Part` path for the common case of one part.
  [#50](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/50)

### Changed

- **Breaking:** `color_mode`'s `basic` setting now offers `sRGB`, `rec709`, `rec1886` and `raw` in
  place of `filmic` and `linear`, on `Read EXR`, `Display EXR Part`, `Display EXR Channel` and `EXR
  Sequence to Preview`, and the default changes from `filmic` to `sRGB`. In v0.2.0 neither retired
  choice applied a transfer function before writing an 8-bit file, so viewers (which assume sRGB)
  crushed shadows in every basic-mode render; `raw` is the like-for-like replacement for `linear`,
  which only clamped, while `sRGB`, `rec709` and `rec1886` now correctly encode. A saved workflow
  using `filmic` or `linear` now fails loudly and names a replacement, rather than silently
  rendering with the old curve. `rec709` and `rec1886` follow Nuke's naming for the same curves.
  [#56](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/56)
- **Breaking:** requires griptape-nodes-engine 0.104.0 or later. `Load EXR`, `Read EXR`,
  `Display EXR Part` and `Display EXR Channel` now launch their "Open in external viewer" button
  through the engine's `LaunchExternalViewerRequest`, which only exists from that release on;
  upgrade the engine before upgrading this library.
  [#38](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/38)
- **Breaking:** `Save EXR`'s `metadata_custom` now rejects writer-owned keys (`compression`, `type`,
  `channels`, `dataWindow`, `displayWindow`, `tiles`, `chunkCount`, `lineOrder`,
  `screenWindowCenter`, `screenWindowWidth`, `chromaticities`) and a value that is malformed JSON
  or valid JSON that isn't an object, by failing the node, instead of writing a file that silently
  disagreed with it: a custom `type` previously wrote a file that failed to reopen, and invalid
  input was dropped with no error. A custom key that duplicates a typed field (such as `owner`) is
  still dropped, now in the typed field's favour, and named in the success details.
  [#44](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/44)
- Bumps `numpy` to `>=2.5.0`, `OpenEXR` to `>=3.5.3`, `pillow` to `>=13.0.0`, and `static-ffmpeg`
  to `>=3.0`.

### Fixed

- **Breaking:** Display EXR Channel blends the `background` in linear light in basic colour mode,
  rather than blending display-encoded values, which darkened soft and antialiased alpha edges.
  Saved workflows with a `background` render brighter edges. The background is read as sRGB and
  shown through the same display transform as the EXR: with the default `sRGB` it looks as
  before, while `rec709` and `rec1886` shift it slightly and `raw` writes it as linear, so it
  looks much darker unless the viewer applies the embedded profile. To keep the background as it
  was, set `tone_mapping` to `sRGB`. `embed_color_profile` now also embeds a profile over a
  background.
  [#54](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/54)
- `EXR Sequence to Preview` in `ocio` mode no longer rescans the whole library/request-handler
  registry on every frame to find the colour transform handler, speeding up long sequences.
  [#42](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/42)
- Fixed `EXR Sequence to Preview` reporting a confusing ffmpeg error ("No such file or directory")
  when every frame in the sequence failed to convert. It now reports the actual per-frame error
  instead.
- Fixed the "Open in external viewer" button on `Read EXR`, `Display EXR Part` and
  `Display EXR Channel`: on Windows, quoted viewer arguments no longer keep their quote
  characters, and a Ctrl+C to the engine console no longer reaches the viewer process. Launching
  from a worker now forwards to the right process instead of launching from the worker itself.
  [#38](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/38)
- `Load EXR` lists channel outputs as `R`, `G`, `B`, `A` first, matching `Display EXR Channel`,
  instead of the alphabetical order (`A`, `B`, `G`, `R`) the file stores them in. Parameter names
  are unchanged, so existing connections still resolve.
  [#51](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/51)
- Fixed EXR preview generation stalling the engine's event loop for the full render (up to
  several seconds on a large file), during which request handling, heartbeats and UI updates were
  blocked.
  [#46](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/46)

[Unreleased]: https://github.com/griptape-ai/griptape-nodes-library-openexr/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/griptape-ai/griptape-nodes-library-openexr/compare/v0.2.0...v0.3.0
