- Fixed `Save EXR`'s `metadata_custom` silently overriding typed metadata fields (and, for keys
  like `compression` or `type`, corrupting the file) instead of being dropped in the typed field's
  favour. Malformed `metadata_custom` now fails the node instead of silently writing a file with
  the metadata missing.
  [#44](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/44)
