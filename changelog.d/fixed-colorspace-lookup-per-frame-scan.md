- `EXR Sequence to Preview` in `ocio` mode no longer rescans the whole library/request-handler
  registry on every frame to find the colour transform handler, speeding up long sequences.
  [#42](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/42)
