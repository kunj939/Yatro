import json, re, sys
S = sys.argv[1]
wm = json.load(open(f"{S}/logo/wm.json"))
r1 = lambda m: f"{float(m.group()):.1f}".rstrip("0").rstrip(".")
D = re.sub(r"-?\d+\.\d+", r1, wm["d"])
cx, cy, r = wm["dots"][0]; cy = -cy; r = r * 1.08
# mark on a 64 grid; the route "y" in white, the destination dot in lime
MARK_ROUTE = "M20 12v11a12 12 0 0 0 24 0v-11M44 12v27a11 11 0 0 1-11 11h-1.5"
MARK_DOT = (20.5, 50, 5.6)
json.dump({"d": D, "dot": [round(cx,1), round(cy,1), round(r,1)], "adv": round(wm["advance"],1),
           "route": MARK_ROUTE, "mdot": MARK_DOT}, open(f"{S}/logo/final.json", "w"))
print(len(D), cx, cy, r, wm["advance"])
