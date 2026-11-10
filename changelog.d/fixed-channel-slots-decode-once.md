- Display EXR Channel and Save EXR read each source file once when several channel slots come
  from the same file, instead of once per slot. With R, G, B, and A connected from one file,
  loading the channels is about four times faster.
  [#74](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/74)
