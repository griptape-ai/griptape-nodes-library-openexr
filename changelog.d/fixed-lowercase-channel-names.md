- Display EXR Part, Read EXR, EXR Sequence to Preview, and EXR previews recognise lowercase or
  whitespace-padded channel names such as `r`, `g`, `b` as RGB, so files storing them as `b`, `g`,
  `r` no longer render with red and blue swapped. Load EXR lists such channels in R, G, B, A order.
  [#68](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/68)
