- **Breaking:** `color_mode`'s `basic` setting now offers `sRGB`, `rec709`, `rec1886` and `raw` in
  place of `filmic` and `linear`, on `Read EXR`, `Display EXR Part`, `Display EXR Channel` and `EXR
  Sequence to Preview`. `linear` applied the sRGB transfer function despite its name; `filmic` was
  Blender-specific vocabulary VFX users do not use. A saved workflow using either value now fails
  loudly and names its replacement, rather than rendering silently with the old curve. `rec709` and
  `rec1886` follow Nuke's naming for the same curves. Default remains `sRGB`.
  [#56](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/56)
