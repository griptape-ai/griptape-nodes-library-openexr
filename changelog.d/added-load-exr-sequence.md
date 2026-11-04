- Added `Load EXR Sequence`, which takes a `Sequence` and outputs an ordered list of
  `EXRPartArtifact` descriptors for every frame's header, without reading pixel data. It connects
  directly into a `ForEach` loop for per-frame work such as `Display EXR Part`, replacing the
  `ScanSequence -> ForEach -> LoadEXR` manual composition.
  [#40](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/40)
