import json, sys, os
S, FE = sys.argv[1], sys.argv[2]
F = json.load(open(f"{S}/logo/final.json"))
cx, cy, r = F["dot"]; mx, my, mr = F["mdot"]; ROUTE = F["route"]; D = F["d"]
TOP, BOT = -1640, 470; W = F["adv"]; H = BOT - TOP
INK, LIME, OLIVE, WHITE = "#101311", "#beff4a", "#659600", "#ffffff"

open(f"{FE}/lib/logo.ts", "w", newline="\n").write(f'''// The Yaitro logo, drawn once and shared by the site, the trip PDF and the share card.
// The mark is a lowercase "y" drawn as a route that ends at a destination dot, on a 64 x 64 tile. The wordmark is
// "yaitro" in Inter Tight ExtraBold, outlined so it looks the same everywhere; its i-dot is the same destination dot.
// Ready-made files for other uses (social profiles, decks, partners) are in public/brand/.
export const MARK = {{
  route: "{ROUTE}",
  stroke: 7.5,
  dot: {{ cx: {mx}, cy: {my}, r: {mr} }},
}};

export const WORDMARK = {{
  viewBox: "0 {TOP} {W} {H}",
  ratio: {W / H:.4f},  // width / height
  d: "{D}",
  dot: {{ cx: {cx}, cy: {cy}, r: {r} }},
}};

export const LOGO_COLORS = {{ ink: "{INK}", lime: "{LIME}", olive: "{OLIVE}" }};
''')

def mark(tile=INK, route=WHITE, dot=LIME, rx=16, scale=1.0, size=None):
    t = "" if scale == 1 else f' transform="translate(32 32) scale({scale}) translate(-32 -32)"'
    sz = f' width="{size}" height="{size}"' if size else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"{sz}><rect width="64" height="64" rx="{rx}" fill="{tile}"/>'
            f'<g{t}><path d="{ROUTE}" fill="none" stroke="{route}" stroke-width="7.5" stroke-linecap="round"/>'
            f'<circle cx="{mx}" cy="{my}" r="{mr}" fill="{dot}"/></g></svg>')

def lockup(text=INK, idot=OLIVE, tile=INK, route=WHITE, dot=LIME):
    wh = 56.5; s = wh / H; gap = 19; ww = W * s; total = 64 + gap + ww
    y0 = 32 - wh / 2
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total:.1f} 64"><title>Yaitro</title>'
            f'<rect width="64" height="64" rx="16" fill="{tile}"/><path d="{ROUTE}" fill="none" stroke="{route}" stroke-width="7.5" stroke-linecap="round"/>'
            f'<circle cx="{mx}" cy="{my}" r="{mr}" fill="{dot}"/>'
            f'<g transform="translate({64 + gap} {y0:.2f}) scale({s:.6f}) translate(0 {-TOP})"><path d="{D}" fill="{text}"/>'
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{idot}"/></g></svg>')

B = f"{FE}/public/brand"; os.makedirs(B, exist_ok=True)
w = lambda p, s: open(p, "w", newline="\n").write(s)
w(f"{FE}/public/favicon.svg", mark())
w(f"{B}/yaitro-mark.svg", mark())
w(f"{B}/yaitro-mark-lime.svg", mark(LIME, INK, INK))
w(f"{B}/yaitro-logo.svg", lockup())
w(f"{B}/yaitro-logo-white.svg", lockup(WHITE, LIME, LIME, INK, INK))
# PNG sources: full-bleed squares (iOS and Android round them), and a profile picture that survives a circle crop
pngs = {"apple-touch-icon.png": (mark(rx=0), 180), "icon-192.png": (mark(), 192), "icon-512.png": (mark(), 512),
        "brand/yaitro-avatar-1024.png": (mark(rx=0, scale=0.82), 1024), "brand/yaitro-avatar-lime-1024.png": (mark(LIME, INK, INK, rx=0, scale=0.82), 1024),
        "brand/yaitro-logo-1600.png": (lockup(), None), "brand/yaitro-logo-white-1600.png": (lockup(WHITE, LIME, LIME, INK, INK), None)}
json.dump({k: v for k, v in pngs.items()}, open(f"{S}/logo/pngs.json", "w"))
print("ok", W, H)
