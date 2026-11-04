- **Breaking:** `color_mode`'s `basic` setting now offers `sRGB`, `rec709`, `rec1886` and `raw` in
  place of `filmic` and `linear`, on `Read EXR`, `Display EXR Part`, `Display EXR Channel` and `EXR
  Sequence to Preview`, and the default changes from `filmic` to `sRGB`. In v0.2.0 neither retired
  choice applied a transfer function before writing an 8-bit file, so viewers (which assume sRGB)
  crushed shadows in every basic-mode render; `raw` is the like-for-like replacement for `linear`,
  which only clamped, while `sRGB`, `rec709` and `rec1886` now correctly encode. A saved workflow
  using `filmic` or `linear` now fails loudly and names a replacement, rather than silently
  rendering with the old curve. `rec709` and `rec1886` follow Nuke's naming for the same curves.
  [#56](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/56)
