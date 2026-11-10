- The part outputs of Load EXR and Load EXR Sequence carry custom header attributes holding `NaN`
  or infinite 32-bit floats, vectors, or matrices as `"NaN"`, `"Infinity"`, or `"-Infinity"`,
  matching the Custom Attributes output, instead of values that are not valid JSON.
  [#70](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/70)
