- Fixed EXR preview generation stalling the engine's event loop for the full render (up to
  several seconds on a large file), during which request handling, heartbeats and UI updates were
  blocked.
  [#46](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/46)
