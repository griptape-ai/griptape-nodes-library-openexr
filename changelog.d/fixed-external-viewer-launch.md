- Fixed the "Open in external viewer" button on `Read EXR`, `Display EXR Part` and
  `Display EXR Channel`: on Windows, quoted viewer arguments no longer keep their quote
  characters, and a Ctrl+C to the engine console no longer reaches the viewer process. Launching
  from a worker now forwards to the right process instead of launching from the worker itself.
  [#38](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/38)
