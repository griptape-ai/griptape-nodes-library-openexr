- `tone_mapping` on Read EXR, Display EXR Part, Display EXR Channel and EXR Sequence to Preview
  offers `ACEScg to sRGB`, `ACEScg to rec709` and `ACES2065-1 to sRGB`, so an ACES plate can be
  viewed in `basic` colour mode without an OCIO config. Each converts ACES primaries to Rec.709
  and applies the named curve; it is not the full ACES output transform, so highlights clip.
  [#103](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/103)
