- Save EXR saves with its default `output_file` instead of failing with "missing required
  variables: _index"; each run writes the next free numbered name, such as `output_v001.exr`.
  A connected File Output Settings node's existing-file setting is now respected.
  [#91](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/91)
