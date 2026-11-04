- **Breaking:** `Save EXR`'s `metadata_custom` now rejects writer-owned keys (`compression`, `type`,
  `channels`, `dataWindow`, `displayWindow`, `tiles`, `chunkCount`, `lineOrder`) and malformed JSON
  by failing the node, instead of writing a file that silently disagreed with it: a custom `type`
  previously wrote a file that failed to reopen, and invalid JSON was dropped with no error. A
  custom key that duplicates a typed field (such as `owner`) is still dropped, now in the typed
  field's favour, and named in the success details.
  [#44](https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/44)
