- `color_mode` on Read EXR, Display EXR Part, Display EXR Channel and EXR Sequence to Preview
  defaults to `basic` on new nodes, whether or not OpenColorIO is loaded, so local tone mapping works
  without setup. Saved workflows keep their mode; select `ocio` to use connected OCIO colour
  parameters.
  [#98](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/98)
