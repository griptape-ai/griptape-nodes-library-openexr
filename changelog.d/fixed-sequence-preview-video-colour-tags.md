- EXR Sequence to Preview converts frames to BT.709 YUV and tags the colour matrix in `ocio` mode,
  avoiding BT.601-to-BT.709 colour shifts on HD and 4K playback while keeping primaries and
  transfer unspecified. MP4 and MOV previews also carry a container `colr` atom matching the stream
  tags.
