- Read EXR's `layer` dropdown labels unprefixed `R`, `G`, `B` and `A` channels `rgba`, and lists
  other unprefixed channels such as `Z` separately as `(other)`. When the file also has an
  `rgba.*` layer, the unprefixed channels show as `(rgba)`. Files without unprefixed `R`, `G`, `B`
  still show `(root)`, and workflows saved with `(root)` keep reading the same colour channels.
  [#121](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/121)
