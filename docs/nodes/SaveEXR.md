---
description: Writes a single-part EXR file from EXR channels, keeping float values, or from an 8-bit image.
when_to_use: Use it to deliver or hand off selected channels as an EXR, or to turn an 8-bit image into an EXR file.
---

## When to use it

You want an EXR file out of the graph. There are two ways to feed it.

- **Channels.** Connect up to four channels from [Read EXR](ReadEXR.md) to `channel_r`, `channel_g`, `channel_b` and `channel_a`. The values are written as floats, so HDR data survives. If any channel slot is connected, `image_in` is ignored, so disconnect the channels if you meant to save the image.
- **Image.** Connect an 8-bit image to `image_in`. Its values are divided by 255 and written as they are, with no conversion to linear. The result is display-referred data in an EXR container. It does not recover highlights that the 8-bit image already clipped.

The file is always a single scanline part. Use `output_part` to preview what was written with [Display EXR Part](DisplayEXRPart.md).

## Example

A comp returns a beauty with a matte for delivery. Use **Get From List** on the `channels` output of Read EXR to select the beauty `R`, `G`, `B` and the matte, connect them to the four channel slots, set `pixel_type` to `HALF` and `compression` to `DWAA` for a small file. In the Metadata group, fill in `metadata_part_name` with the pass name and `metadata_software` with the studio's tool name.

## Parameters

### image_in

The image is converted to RGB. An alpha channel in a PNG is dropped. Only the channel slots can write alpha.

### channel_r

The channel is written under the name `R`, whatever it was called in the source. The same applies to `channel_g`, `channel_b` and `channel_a`, with their own names. You can connect one slot only, for example to save a single depth channel as `G`.

All connected channels must have the same size, or the run fails. They are not aligned by data window. The written file's data window and display window both start at 0,0 and take the size of the channels, so overscan offsets from the source are not carried over. Source header attributes other than those set in the Metadata group are also not copied.

### compression

The codec. Lossless choices are `NONE`, `RLE`, `ZIPS`, `ZIP` (the default), `PIZ`, `HTJ2K32`, `HTJ2K256` and `ZSTD`. `PXR24` is lossy for `FLOAT` pixels, `B44` and `B44A` for `HALF` pixels, and `DWAA`, `DWAB` and `LJ2K` for both. Use a lossy codec for beauty renders where size matters. Keep a lossless one for data passes such as depth, normals and position, where exact values matter.

### pixel_type

Applies to every channel in the file. `HALF` is 16-bit float. `FLOAT` is 32-bit. A channel that was stored as `FLOAT` in the source is reduced to half precision when `pixel_type` is `HALF`, so choose `FLOAT` for depth and position passes. `FLOAT` files are larger.

### metadata_part_name

The name of the part. Left empty, no name is written.

### metadata_pixel_aspect_ratio

Written only when it is not `1.0`.

### metadata_capture_date

Stored as text in the file header. The node does not check the format.

### metadata_custom

A JSON object of extra header attributes. Keys that match a dedicated Metadata field, by EXR attribute name, are dropped in favour of that field, even when the field is empty. The names are `name`, `pixelAspectRatio`, `owner`, `comments`, `capDate`, `software` and `timeCode`. The result details list any that were dropped.

Keys that describe the file's structure fail the run: `compression`, `type`, `channels`, `dataWindow`, `displayWindow`, `tiles`, `chunkCount`, `lineOrder`, `screenWindowCenter`, `screenWindowWidth` and `chromaticities`. A value that is not a JSON object also fails.

### output_file

The file name for the EXR, resolved through the project's output settings.

### output_part

A descriptor for the single part that was written. It points at the saved file and lists its channels.
