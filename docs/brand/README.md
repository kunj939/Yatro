# Yaitro logo

- **Mark:** a lowercase "y" drawn as a route that ends at a destination dot, on a rounded tile. Dark tile (white route,
  lime dot) on light backgrounds; lime tile (ink route and dot) on dark backgrounds.
- **Wordmark:** "yaitro" in Inter Tight ExtraBold (tracking -0.018 em, a little extra space after the r), outlined to
  paths. The i-dot is the destination dot: olive `#659600` on light, lime `#beff4a` on dark.
- **Colours:** ink `#101311`, lime `#beff4a`, olive `#659600`.
- **Files:** `frontend/public/brand/` (SVG and PNG lockups, marks, 1024 px profile pictures that survive a circle crop),
  `frontend/public/favicon.svg`, `apple-touch-icon.png`, `icon-192.png`, `icon-512.png`. Code reads the shapes from
  `frontend/lib/logo.ts`.

## Regenerating

Needs Python with `fonttools` and `uharfbuzz`, and Inter Tight's variable font (`InterTight[wght].ttf` from google/fonts).

    python outline.py InterTight.ttf <work>/logo/it800 "yaitro" 800 -0.018 > <work>/logo/wm.json
    python build.py <work>          # writes <work>/logo/final.json (mark geometry lives here)
    python emit.py <work> frontend  # writes lib/logo.ts, favicon.svg, public/brand/*.svg and the PNG sources list

The PNGs were rendered from the SVGs with headless Chrome (`--default-background-color=00000000 --screenshot`).
