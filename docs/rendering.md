# Rendering And Colour Management

## OpenColorIO

Basic mode uses the display transforms below. For a pipeline-specific transform,
install the [OpenColorIO library](https://github.com/griptape-ai/griptape-nodes-library-opencolorio).
Connect its colour parameters to the EXR node and set `color_mode` to `ocio`:

```mermaid
flowchart LR
    config["Load OCIO Config"] -->|config -> config| params["OCIO Color Parameters"]
    params -->|color_params -> color_params| read["Read EXR"]
```

Load OCIO Config uses `$OCIO`, or an explicit path set through **Advanced > Override
OCIO Config** and **Config File Path**. In OCIO Color Parameters, select the source
colourspace, display and view. The same `color_params` output can connect to
Display EXR Part, Display EXR Channel or EXR Sequence to Preview.

## Display Transforms

In `basic` colour mode, `tone_mapping` selects how scene-linear values are
encoded for display. Each name gives the source colour space and the display
encoding. The encodings match the views in Nuke's default OCIO config, so
`rec709` and `rec1886` mean the same curves there as they do here.

| Choice                   | Curve                                    | Linear 0.18 renders as |
| ------------------------ | ---------------------------------------- | ---------------------- |
| `Linear sRGB to sRGB`    | sRGB transfer function (IEC 61966-2-1)   | 118                    |
| `Linear sRGB to rec709`  | BT.709 OETF                              | 104                    |
| `Linear sRGB to rec1886` | Zero-black BT.1886 (gamma 2.4)           | 125                    |
| `raw`                    | None. Clamped to [0, 1] with no encoding | 46                     |

Workflows saved with the earlier names `sRGB`, `rec709` and `rec1886` load
with the matching choice above and render the same.

`rec709` is darker than `sRGB` through the midtones and `rec1886` is lighter,
because sRGB's effective gamma of roughly 2.2 sits between the two. `raw`
applies no transfer function, which is useful for reading actual pixel values
but looks dark on screen.

These four assume linear Rec.709/sRGB primaries. For an ACES plate, pick a transform that converts its
primaries to Rec.709 first and then applies one of the curves above:

| Choice               | Source primaries | Then encodes as |
| -------------------- | ---------------- | --------------- |
| `ACEScg to sRGB`     | ACEScg (AP1)     | `sRGB`          |
| `ACEScg to rec709`   | ACEScg (AP1)     | `rec709`        |
| `ACES2065-1 to sRGB` | ACES2065-1 (AP0) | `sRGB`          |

The conversion matrix is calculated in
[`colour_spaces.py`](../griptape_nodes_openexr/exr/colour/colour_spaces.py) from the published
[ACEScg](https://docs.acescentral.com/encodings/acescg/) and
[ACES2065-1](https://docs.acescentral.com/encodings/aces2065-1/) chromaticities and the
[ITU-R BT.709](https://www.itu.int/rec/R-REC-BT.709-6-201506-I/en) primaries and white point.
A [Bradford adaptation](https://www.color.org/chadtag.xalter) maps the ACES reference white
point to the destination D65 white point, so neutral greys stay neutral. Colours outside
Rec.709 clip. Highlights clip at the display encoding stage. Use `ocio` mode for an ACES
output transform with tone mapping and highlight roll-off; these basic conversions will
produce a different image from an OCIO ACES view.

No choice applies a tone curve, so scene values above 1.0 clip to white.
Nuke's default viewer behaves the same way. Lower the `exposure` to bring
highlights into range, or use `ocio` mode with a config that provides a
tone-mapped view transform such as ACES.

Without an embedded profile, viewers decode the PNG as sRGB, so picking
`rec709` or `rec1886` shows the same mismatch you get when viewing a Rec.709
output on an sRGB monitor. See [Colour profiles](rendering.md#colour-profiles).

> **Note:** `linear` and `filmic` were removed, and workflows still set to
> either fail with an error naming the replacement.
>
> `linear` applied the sRGB transfer function despite its name, making it a
> confusing spelling of `sRGB`. Use `Linear sRGB to sRGB` to keep the previous output, or
> `raw` for genuinely unencoded linear values.
>
> `filmic` was a Narkowicz 2015 approximation of the ACES RRT, a term from
> Blender and real-time rendering rather than VFX. Basic mode now offers only
> display encodings, matching Nuke's default viewer. Use `Linear sRGB to sRGB` for a similar
> look, or `ocio` mode for a real tone-mapped view transform.

## Display Window

Use `window_mode` to choose which region the node displays. Supported by **Read EXR**, **Display EXR Part**, **Display EXR Channel** and **EXR Sequence to Preview**.

<details>
<summary>Info: EXR data and display windows</summary>

An EXR's _data window_ contains its stored pixels. The _display window_ defines the intended frame. Stored pixels can extend beyond the frame (overscan) or cover only part of it.

</details>

| `window_mode`            | Rendered image                                                                                                                                        |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Full` (default)         | Every stored pixel, at the data window's size.                                                                                                        |
| `Crop Overscan`          | The display window's size. Overscan is cropped away, and frame areas with no stored pixels are black, with zero alpha in RGBA output.                 |
| `Show Overscan Boundary` | Every stored pixel, with a dashed black-and-white outline just inside the display window's edge. Edges that fall outside the data window are clipped. |

Only the rendered PNG or video changes. The `part` and `channels` outputs describe the original EXR and retain its data window, display window and source pixels. Downstream display nodes choose their own `window_mode`; set the same mode and intended frame there to match Read EXR's rendered dimensions. Read EXR's `image_width` and `image_height` describe its rendered image, while dimensions on `part` describe the source data window.

The automatic file-level EXR preview uses the data window and receives no node-specific rendering settings. It can show overscan even when Read EXR renders with `Crop Overscan`. This preview is separate from the node's rendered `image` output.

**Load EXR** and **Load EXR Sequence** report both windows as stored. For a sequence, each frame is framed by its own header, so a changing data window renders at one size in `Crop Overscan` mode when the display window stays constant. Workflows saved before this option existed load with `Full`, so they render as they did.

## Colour Profiles

By default the written PNG carries no colour profile, so viewers read it as
sRGB whichever transform produced it. That is what makes `Linear sRGB to rec709`
and `Linear sRGB to rec1886` look different on screen: the file is encoded with
one curve and decoded with another, the same mismatch you get sending a Rec.709
output to an sRGB monitor.

Setting `embed_color_profile` writes an ICC profile naming the Rec.709 primaries
and transfer curve used. The pixels are untouched; only the label changes.

With a profile embedded, transforms that assume the same source primaries look
the same in a colour-managed viewer. Once the file says how its Rec.709 pixels
were encoded, a managed application decodes that curve and converts to your
display, so `Linear sRGB to sRGB`, `Linear sRGB to rec709` and
`Linear sRGB to rec1886` match on screen, and `ACEScg to sRGB` matches
`ACEScg to rec709`. Transforms with different source primaries still render
differently because they apply different gamut conversions before encoding.

Choose `embed_color_profile` according to how you will use the output:

| `embed_color_profile` | Use for                                                           |
| --------------------- | ----------------------------------------------------------------- |
| `false` (default)     | An on-screen preview that emulates a Rec.709 or BT.1886 monitor.  |
| `true`                | A file another application will open, convert or grade correctly. |

The parameter is hidden in `ocio` colour mode, where the display/view
transform already resolves to a display colorspace we cannot describe.

**Video** is always tagged with CICP stream and `colr` container metadata, and
has no `embed_color_profile` parameter because YUV video players use CICP
signalling rather than RGB ICC profiles. In `basic` mode **EXR Sequence to
Preview** converts frames to BT.709 YUV and tags Rec.709 primaries, the BT.709
matrix and the matching transfer characteristic. In `ocio` mode it converts
frames to BT.709 YUV and tags the BT.709 matrix while leaving primaries and
transfer unspecified, since the OCIO display/view transform can target any
display space.

Alpha is included automatically when an alpha channel (`A`) is found in the part.
