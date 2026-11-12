- Display EXR Channel and Save EXR take a channel list on `channels`, so Read EXR connects directly,
  without a Get From List per channel. The list fills R, G, B and A by channel name; a channel
  connected to a slot replaces the one from the list. Read EXR adds a `layer_channels` output holding
  the selected layer's channels, so the downstream node gets the layer Read EXR renders.
  [#104](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/104)
