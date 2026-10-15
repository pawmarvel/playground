# Static product profiles

These profiles are the operator and bundle generator's source of truth for
preview canvases and exact print targets. Pixel dimensions are authoritative.
All current targets use PNG, sRGB, transparent padding, and 300 DPI metadata;
vendor requirements should be confirmed before the first production release.

For apparel requirements supplied as “1080 wide,” preview height is rounded to
the nearest pixel while preserving the print-target aspect ratio:

- unisex tee/crewneck/hoodie/tanks: `1080x1430`;
- women's Bella 6400: `1080x1431`; and
- men's house style: `1080x1234`.

Multi-target profiles produce one bundle containing every declared target.
The first target is the default source used to derive the bundle preview.
