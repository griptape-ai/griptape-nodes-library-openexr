- `tone_mapping` choices that assume linear sRGB input are named for it: `Linear sRGB to sRGB`,
  `Linear sRGB to rec709` and `Linear sRGB to rec1886` across the EXR display nodes and the EXR
  Preview artifact generator. Saved workflows and preview settings using `sRGB`, `rec709` or
  `rec1886` load with the new name and render the same.
  [#103](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/103)
