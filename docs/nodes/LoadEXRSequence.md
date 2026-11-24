---
description: Deprecated. Scans the headers of every frame in an EXR sequence and outputs one part descriptor per frame. Use Read EXR for new workflows.
when_to_use: Use it only to keep an existing workflow running, or when you need the whole list of frame descriptors, layout validation or parallel header scans.
---

## When to use it

This node is deprecated. Use [Read EXR](ReadEXR.md) for new sequence workflows: connect the `Sequence` to its `path` and drive `frame` with real frame numbers. Load EXR Sequence stays available while the missing features are moved across, with no removal date. The [migration guide](../migration.md) says what Read EXR covers and when to keep this node.

The node scans headers only, whatever `openexr.header_only` says, and never reads pixels. Its `parts` output is a plain list, so it can feed a **For Each** loop that runs [Display EXR Part](DisplayEXRPart.md) on every frame.

## Parameters

### sequence

Takes a `Sequence` from **Scan Sequence** or **Scan Split Sequence**. A typed path is not accepted here.

### part_index

The same part is read from every frame, counting from 0. Frames with several parts need one node for each part you want. A frame without this part counts as a failed scan.

### on_mismatch

Frames are compared with the resolution and channel layout that most frames share, so an outlier such as a slate frame is the one that is dropped. If no layout covers more than half of the scanned frames, the node fails whatever this is set to. Frames that failed to scan do not count in that comparison. With `skip`, dropped frames are reported in the result details. With `fail`, any dropped frame fails the run.

### parts

Each descriptor has a `frame_number` field with the real frame number. With `skip`, the list can be shorter than `frame_count`, and a frame's position in the list no longer matches its number. Use `frame_number`, not the list position, to name output files or find the same frame elsewhere.
