# Approved authoring font catalog

Each immediate child directory is one redistributable font family and must
contain one or more `.ttf` files plus its unmodified SIL Open Font License as
`OFL.txt`. `pawmarvel-layout-config --font-catalog assets/fonts` validates the
entire catalog before opening the editor and presents every approved font for
human comparison.

`expanded-catalog.json` is a lightweight, versioned remote index. In
`--font-catalog-mode expanded`, the tool downloads only its bounded candidate
set into `.pawmarvel-font-cache`, verifies pinned SHA-256 hashes and OFL 1.1
text, and ranks those candidates together with this local fallback catalog.
The index pins an immutable Google Fonts commit; do not replace its URLs with
mutable branch URLs.

The selected family is copied into the template and is the only font published
in the runtime bundle. Do not place system, proprietary, or license-ambiguous
fonts in this directory.
