- Load EXR reports ZSTD, HTJ2K, and LJ2K compressed files by their codec instead of
  `NO_COMPRESSION`. A file whose compression, line order, or storage type the library does not
  recognise fails to load with an error naming the value, instead of reporting a wrong one.
  [#69](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/69)
