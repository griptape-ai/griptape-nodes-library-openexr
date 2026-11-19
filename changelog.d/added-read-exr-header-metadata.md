- `Read EXR` exposes ten header metadata outputs for the selected frame and part, including
  compression, source windows, owner, chromaticities, and custom attributes. Metadata refreshes
  when the source, frame, or part changes; failed executions clear it to defaults. Setters return
  before metadata finishes refreshing; each node reads independently and execution reads fresh
  headers. If the selection changes during a run, the run follows the Failed control output
  and discards its image results while preserving metadata refreshed for the newer selection.
  https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/111
