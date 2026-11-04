- Display EXR Channel blends the `background` in linear light in basic colour mode, rather than
  blending display-encoded values, which darkened soft and antialiased alpha edges. The background
  is read as sRGB and shown through the same display transform as the EXR, so `rec709`, `rec1886`
  and `raw` change how it looks, and `embed_color_profile` now also embeds a profile over a
  background.
  [#54](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/54)
