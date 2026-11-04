- **Breaking:** Display EXR Channel blends the `background` in linear light in basic colour mode,
  rather than blending display-encoded values, which darkened soft and antialiased alpha edges.
  Saved workflows with a `background` render brighter edges. The background is read as sRGB and
  shown through the same display transform as the EXR: with the default `sRGB` it looks as
  before, while `rec709` and `rec1886` shift it slightly and `raw` writes it as linear, so it
  looks much darker unless the viewer applies the embedded profile. To keep the background as it
  was, set `tone_mapping` to `sRGB`. `embed_color_profile` now also embeds a profile over a
  background.
  [#54](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/54)
