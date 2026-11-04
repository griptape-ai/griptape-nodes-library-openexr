- `Read EXR`, `Display EXR Part` and `Display EXR Channel` have a new `embed_color_profile`
  parameter (off by default). When on, the written PNG carries an ICC profile naming the `basic`
  display transform used, so colour-managed applications like Photoshop or a browser convert it
  correctly instead of reading every transform as sRGB. `EXR Sequence to Preview` instead always
  tags its video output's colour transfer characteristics, since an MP4 cannot carry an ICC profile.
