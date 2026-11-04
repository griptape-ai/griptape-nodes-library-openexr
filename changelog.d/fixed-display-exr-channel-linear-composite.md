- Display EXR Channel blends the `background` in linear light in basic colour mode, rather than
  blending display-encoded values, which darkened soft and antialiased alpha edges. The background
  is read as sRGB and shown through the same display transform as the EXR: with the default
  `sRGB` it looks as before, while `rec709` and `rec1886` shift it slightly and `raw` writes it as
  linear, so it looks much darker unless the viewer applies the embedded profile.
  `embed_color_profile` now also embeds a profile over a background.
  [#54](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/54)
