- Added `Read EXR`, a single node that loads a path or `Sequence` and emits a viewable image
  alongside the typed `EXRPartArtifact` and `EXRChannelArtifact` descriptors, replacing the
  `Load EXR -> Get From List -> Display EXR Part` path for the common case of one part.
  [#50](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/50)
