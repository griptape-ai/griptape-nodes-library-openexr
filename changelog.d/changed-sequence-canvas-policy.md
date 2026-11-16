- **Breaking:** EXR Sequence to Preview rejects differing rendered frame sizes by default, replacing
  ffmpeg's automatic resizing. Choose `frame_sizes=Pad to largest` to preserve all frames with black
  padding. `odd_dimensions` defaults to `Pad to even`, adding black pixels on the right and bottom
  when needed for H.264 encoding; choose `Require even` to reject odd encoder dimensions.
