# Changelog

All notable changes to the OpenEXR Library will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/2.0.0/), and each version is
the `library_version` in `griptape-nodes-library.json`.
**Breaking** marks a change that can stop a saved workflow from working without edits.

## [Unreleased]

<!-- Entries go in changelog.d/, one file each. See changelog.d/README.md. -->

## [0.5.0] - 2026-11-24

### Added

- `tone_mapping` on Read EXR, Display EXR Part, Display EXR Channel and EXR Sequence to Preview
  offers `ACEScg to sRGB`, `ACEScg to rec709` and `ACES2065-1 to sRGB`, so an ACES plate can be
  viewed in `basic` colour mode without an OCIO config. Each converts ACES primaries to Rec.709
  and applies the named curve; it is not the full ACES output transform, so highlights clip.
  [#103](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/103)
- Display EXR Channel has `channel_alignment` options: `Match Pixel Positions`,
  `Match EXR Coordinates`, and `Require Matching Windows`. `frame_reference` selects which channel
  defines the intended frame, defaulting to `First Connected RGB`. The default alignment preserves
  stored pixel positions.
- Read EXR, Display EXR Part, Display EXR Channel and EXR Sequence to Preview have a `window_mode`
  option. `Crop Overscan` renders only the intended frame, cropping overscan and filling frame areas
  with no pixels with black, with zero alpha in RGBA output. `Show Overscan Boundary` shows every
  stored pixel and outlines the frame. The default, `Full`, renders as before.
  [#97](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/97)
- The library now ships a docs page for each node, shown in the editor's Docs panel, along with a
  library overview. Engines older than 0.110.0 ignore these pages.
- `EXR Sequence to Preview` accepts an EXR file path or frame pattern directly in `sequence`,
  with a file picker. Connected Sequences take precedence; direct paths skip absent frame numbers.
  https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/114

### Changed

- **Breaking:** Display EXR Channel and EXR Sequence to Preview reject output or intermediate render
  canvases above 67,108,864 pixels (8192x8192) by default. Raise `openexr.max_render_pixels` in the
  library settings to run saved workflows that render larger canvases. Source EXR decoding remains
  unrestricted.
- **Breaking:** EXR Sequence to Preview rejects differing rendered frame sizes by default, replacing
  ffmpeg's automatic resizing. Choose `frame_sizes=Pad to Largest` to preserve all frames with black
  padding. `odd_dimensions` defaults to `Pad to Even`, adding black pixels on the right and bottom
  when needed for H.264 encoding; choose `Require Even` to reject odd encoder dimensions.
- `color_mode` on Read EXR, Display EXR Part, Display EXR Channel and EXR Sequence to Preview
  defaults to `basic` on new nodes, whether or not OpenColorIO is loaded, so local tone mapping works
  without setup. Saved workflows keep their mode; select `ocio` to use connected OCIO colour
  parameters.
  [#98](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/98)
- The ICC profile written by `embed_color_profile` on Read EXR, Display EXR Part and Display EXR
  Channel has a copyright tag naming The Foundry Visionmongers Ltd. and stating that the profile
  may be copied, distributed and used without restriction. It previously read "Public Domain".
  Pixel values are unchanged.
- `tone_mapping` choices that assume linear sRGB input are named for it: `Linear sRGB to sRGB`,
  `Linear sRGB to rec709` and `Linear sRGB to rec1886` across the EXR display nodes and the EXR
  Preview artifact generator. Saved workflows and preview settings using `sRGB`, `rec709` or
  `rec1886` load with the new name and render the same.
  [#103](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/103)

### Deprecated

- `Load EXR` and `Load EXR Sequence` are deprecated with warnings and remain available during
  migration, with no scheduled removal release. Use `Read EXR` for new file and sequence reading
  workflows. Missing functionality is being migrated to it; the migration guide in `docs/migration.md` explains
  when to keep the retained nodes.
  [#112](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/112)

### Fixed

- Read EXR, Display EXR Part and EXR Sequence to Preview report unsupported basic-mode
  `tone_mapping` values before scanning or decoding source images. OCIO mode continues to ignore
  the hidden `tone_mapping` value.
- `embed_color_profile` on Read EXR, Display EXR Part and Display EXR Channel writes the D65
  display white point in `wtpt` with a Bradford D65-to-D50 `chad` adaptation tag and exact
  D50-summing primary XYZ columns.
- `Read EXR` reports the sequence scanner's error in `result_details` when a path or pattern
  cannot be loaded, including paths with no matching frames.
  https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/114
- EXR Sequence to Preview converts frames to BT.709 YUV and tags the colour matrix in `ocio` mode,
  avoiding BT.601-to-BT.709 colour shifts on HD and 4K playback while keeping primaries and
  transfer unspecified. MP4 and MOV previews also carry a container `colr` atom matching the stream
  tags.
- When the installed OpenEXR binary is older than the library needs, the library fails to load
  with a message naming the installed and required OpenEXR versions and the reinstall command,
  instead of a bare codec name such as `LJ2K_COMPRESSION`.
  [#102](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/102)

## [0.4.0] - 2026-11-11

### Added

- Load EXR shows a warning after a header-only scan, since every channel is then labelled `half`, and
  says to set `openexr.header_only` to `false` and reselect the file to read the true pixel types.
  [#65](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/65)
- Save EXR's Compression dropdown offers every codec Load EXR reads: RLE, PXR24, B44, B44A, DWAB,
  HTJ2K32, HTJ2K256, LJ2K, and ZSTD join ZIP, ZIPS, PIZ, DWAA, and NONE. The tooltip says which
  codecs are lossy.
  [#81](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/81)
- EXR Sequence to Preview has a `missing_frames` option for frames that fail to convert: `skip`
  leaves them out, `error` fails the node and lists them, and `black` puts a black frame in their
  place so the video keeps the sequence's length.
  [#66](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/66)

### Fixed

- **Breaking:** Display EXR Part, Read EXR, EXR Sequence to Preview, and EXR previews recognise
  lowercase or whitespace-padded channel names such as `r`, `g`, `b`, `a` as RGBA, so files storing
  them as `b`, `g`, `r` no longer render with red and blue swapped, and a lowercase `a` is now
  applied as alpha. Load EXR lists such channels in R, G, B, A order. Saved workflows reading these
  files render with corrected colours and transparency; remove any channel swap added to work
  around the old order.
  [#68](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/68)
- Display EXR Channel and Save EXR read each source file once when several channel slots come
  from the same file, instead of once per slot. With R, G, B, and A connected from one file,
  loading the channels is about four times faster.
  [#74](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/74)
- Load EXR reports ZSTD, HTJ2K, and LJ2K compressed files by their codec instead of
  `NO_COMPRESSION`. A file whose compression, line order, or storage type the library does not
  recognise fails to load with an error naming the value, instead of reporting a wrong one.
  [#69](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/69)
- Load EXR labels each channel with its real pixel type (`half`, `float`, or `uint`) when the
  `openexr.header_only` setting is `false`, instead of labelling every channel `half`.
  [#65](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/65)
- The part outputs of Load EXR and Load EXR Sequence carry custom header attributes holding `NaN`
  or infinite 32-bit floats, vectors, or matrices as `"NaN"`, `"Infinity"`, or `"-Infinity"`,
  matching the Custom Attributes output, instead of values that are not valid JSON.
  [#70](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/70)
- Save EXR saves with its default `output_file` instead of failing with "missing required
  variables: _index"; each run writes the next free numbered name, such as `output_v001.exr`.
  A connected File Output Settings node's existing-file setting is now respected.
  [#91](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/91)
- EXR Sequence to Preview keeps every frame that converts when one frame in the middle fails,
  instead of ending the video at the failed frame, and its result details report how many frames
  were encoded and which failed.
  [#66](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/66)

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

[Unreleased]: https://github.com/griptape-ai/griptape-nodes-library-openexr/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/griptape-ai/griptape-nodes-library-openexr/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/griptape-ai/griptape-nodes-library-openexr/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/griptape-ai/griptape-nodes-library-openexr/compare/v0.2.0...v0.3.0
