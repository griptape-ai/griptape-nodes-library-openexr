- **Breaking:** Display EXR Channel and EXR Sequence to Preview reject output or intermediate render
  canvases above 67,108,864 pixels (8192x8192) by default. Raise `openexr.max_render_pixels` in the
  library settings to run saved workflows that render larger canvases. Source EXR decoding remains
  unrestricted.
