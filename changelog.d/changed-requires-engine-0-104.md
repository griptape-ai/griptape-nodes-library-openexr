- **Breaking:** requires griptape-nodes-engine 0.104.0 or later. `Load EXR`, `Read EXR`,
  `Display EXR Part` and `Display EXR Channel` now launch their "Open in external viewer" button
  through the engine's `LaunchExternalViewerRequest`, which only exists from that release on;
  upgrade the engine before upgrading this library.
  [#38](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/38)
