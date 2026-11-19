# Third-party notices and colour-source acknowledgements

The library's Python implementation is distributed under the Apache License 2.0 in
[LICENSE](LICENSE). The source acknowledgements below identify material used in its colour
calculations. Each source retains its own terms.

## ACES colour-space data

Academy Color Encoding System (ACES) is provided by the Academy of Motion Picture Arts and Sciences.

Copyright Contributors to the ACES Documentation

The AP0 and AP1 primary chromaticities and ACES white-point coordinates used in
`griptape_nodes_openexr/exr/colour/colour_spaces.py` are reproduced from these ACES documentation
pages at revision `ef03499b58a349de4d09c4c739742dab4ca61122`:

- [ACES2065-1 Specification](https://github.com/aces-aswf/aces-docs/blob/ef03499b58a349de4d09c4c739742dab4ca61122/docs/encodings/aces2065-1.md),
  sections "Color primaries" and "White point".
- [ACEScg](https://github.com/aces-aswf/aces-docs/blob/ef03499b58a349de4d09c4c739742dab4ca61122/docs/encodings/acescg.md),
  section "Color space chromaticities".

These pages identify their licence as Creative Commons Attribution 4.0 International
([CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)). The complete upstream licence text is
included verbatim in [licenses/ACES-DOCUMENTATION-CC-BY-4.0.txt](licenses/ACES-DOCUMENTATION-CC-BY-4.0.txt).

The numeric coordinates are unchanged. They are represented as Python tuples, and the library
calculates conversion matrices to Rec.709 using local NumPy code and Bradford white-point
adaptation. The derived matrices are stored as float32 arrays. These are basic gamut conversions
followed by display encoding; they include no ACES output transform or highlight roll-off.

The ACES2065-1 documentation references
[SMPTE ST 2065-1:2021](https://doi.org/10.5594/SMPTE.ST2065-1.2021). Source comments also identify
Academy S-2014-004 as the ACEScg specification. Neither standards document is redistributed here.
The immediate sources for the ACES coordinates are the documentation pages identified above.

No Academy CTL source code, ACES core software, ACES logo or colour-science Python implementation
is bundled with this library. The names ACEScg and ACES2065-1 identify input colour spaces.
This library makes no ACES certification claim and implies no endorsement by the Academy or
other source contributors.

## Rec.709 and BT.1886

The Rec.709 primary chromaticities and D65 white-point coordinates are taken from
[ITU-R BT.709-6 (June 2015)](https://www.itu.int/rec/R-REC-BT.709-6-201506-I/en),
Table 1, sections 1.3 and 1.4. Section 1.2 supplies the opto-electronic transfer characteristic
used by the Rec.709 display encoding.

The zero-black display encoding uses the inverse EOTF from
[ITU-R BT.1886-0 (March 2011)](https://www.itu.int/rec/R-REC-BT.1886/en), Annex 1.

The publications carry ITU copyright notices for 2015 and 2011 respectively, with all rights
reserved. The library implements the numeric definitions; it does not redistribute the publications.
This acknowledgement grants no rights to reproduce ITU publications.

## Bradford adaptation and ICC profiles

The Bradford coefficients and adaptation method are described by the International Color Consortium
in ["The 'chad' Tag"](https://www.color.org/chadtag.xalter), which references section E1.2 of the
ICC v4 specification. The page's footer identifies the International Color Consortium as copyright
holder and displays the current year. Its [Terms of Service](https://www.color.org/terms-of-service/)
apply to website material.

The library implements the numerical adaptation and builds ICC profile bytes locally. The ICC
reference page and specification are not redistributed. This acknowledgement grants no rights to
reproduce ICC website material or specifications.

## sRGB

The sRGB transfer-function definition is attributed to IEC 61966-2-1:1999. The library implements
its piecewise numeric encoding and decoding and generates its own uint8 decoding lookup table.
The IEC standard is not redistributed.
