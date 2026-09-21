# Lantern brand assets

The canonical mark lives in two places in this repository:
`apps/sage-website/components/lantern-mark.tsx` (React, `currentColor`, used in
the site chrome) and `public/favicon.svg` (the same 48×48 geometry standalone,
amber). Everything in this directory is that standalone geometry — no redraw —
exported for film, social and channel art.

## Mark

Four colourways, each as an SVG plus PNGs at 1024, 512 and 256:

| variant | ink | background | use |
|---|---|---|---|
| `lantern-mark-transparent-amber` | `#ffbe62` | transparent (RGBA) | over footage or an unknown ground |
| `lantern-mark-transparent-navy` | `#0b1820` | transparent (RGBA) | on light grounds |
| `lantern-mark-navy-bg-amber` | `#ffbe62` | `#0b1820` | the last-beat lockup |
| `lantern-mark-amber-bg-navy` | `#0b1820` | `#ffbe62` | the inverse, matching the site's CTA band |

Brand colours in the sources: navy `#0b1820` (the ink), amber `#ffbe62` (the
accent). No other palette is used for the mark.

Regenerate with `work/website-mktg-assets-20260921/export-mark.py <out-dir>`
(needs `rsvg-convert`); it reads `public/favicon.svg` and asserts the exported
amber mark still matches it.

## Type

The site loads **Geist** and **Geist Mono** as variable fonts through
`next/font/google` (`app/layout.tsx`, latin subset, no weight list). Both are
OFL-1.1:

* `https://raw.githubusercontent.com/google/fonts/main/ofl/geist/Geist%5Bwght%5D.ttf`
  (sha256 `73894e0448cae90a92b6c2f8732b7bb9acb7b94c418bff559dad4a18e1de9659`)
* `https://raw.githubusercontent.com/google/fonts/main/ofl/geistmono/GeistMono%5Bwght%5D.ttf`
  (sha256 `d00e590b8eb3a59acc329b2d044fd143ae935090b7da33199ebee27cc7de8196`)
* licence text beside them: `https://raw.githubusercontent.com/google/fonts/main/ofl/geist/OFL.txt`

Fonts are not committed here — they are fetched from upstream, so the licence
text travels with them rather than being restated. See `WEIGHTS.md` for the
weights the site actually uses.
