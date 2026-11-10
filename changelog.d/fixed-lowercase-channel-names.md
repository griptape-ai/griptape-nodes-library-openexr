- **Breaking:** Display EXR Part, Read EXR, EXR Sequence to Preview, and EXR previews recognise
  lowercase or whitespace-padded channel names such as `r`, `g`, `b`, `a` as RGBA, so files storing
  them as `b`, `g`, `r` no longer render with red and blue swapped, and a lowercase `a` is now
  applied as alpha. Load EXR lists such channels in R, G, B, A order. Saved workflows reading these
  files render with corrected colours and transparency; remove any channel swap added to work
  around the old order.
  [#68](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/68)
