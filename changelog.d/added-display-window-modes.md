- Read EXR, Display EXR Part, Display EXR Channel and EXR Sequence to Preview have a `window_mode`
  option. `display window` renders only the intended frame, cropping overscan and filling frame areas
  with no pixels with black. `data window with display overlay` shows every stored pixel and outlines
  the frame. The default, `data window`, renders as before.
  [#97](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/97)
