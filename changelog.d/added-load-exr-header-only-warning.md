- Load EXR shows a warning after a header-only scan, since every channel is then labelled `half`, and
  says to set `openexr.header_only` to `false` and reselect the file to read the true pixel types.
  [#65](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/65)
