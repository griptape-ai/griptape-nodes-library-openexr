- EXR Sequence to Preview has a `missing_frames` option for frames that fail to convert: `skip`
  leaves them out, `error` fails the node and lists them, and `black` puts a black frame in their
  place so the video keeps the sequence's length.
  [#66](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/66)
