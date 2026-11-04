- Fixed `EXR Sequence to Preview` reporting a confusing ffmpeg error ("No such file or directory")
  when every frame in the sequence failed to convert. It now reports the actual per-frame error
  instead.
