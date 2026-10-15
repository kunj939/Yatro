import sys, json
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from fontTools.pens.recordingPen import DecomposingRecordingPen as RecordingPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.boundsPen import BoundsPen
import uharfbuzz as hb

src, out, text, wght, track = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4]), float(sys.argv[5])
f = TTFont(src); f = instantiateVariableFont(f, {"wght": wght})
f.save(out + ".ttf")
blob = hb.Blob.from_file_path(out + ".ttf"); face = hb.Face(blob); font = hb.Font(face)
buf = hb.Buffer(); buf.add_str(text); buf.guess_segment_properties(); hb.shape(font, buf, {"kern": True, "liga": False})
upm = f["head"].unitsPerEm; gs = f.getGlyphSet(); order = f.getGlyphOrder()
x = 0; ink = []; dots = []
for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
    name = order[info.codepoint]; rec = RecordingPen(gs); gs[name].draw(rec)
    # split into contours
    contours, cur = [], []
    for op, args in rec.value:
        cur.append((op, args))
        if op in ("closePath", "endPath"): contours.append(cur); cur = []
    for c in contours:
        bp = BoundsPen(gs); 
        for op, args in c: getattr(bp, op)(*args)
        xmin, ymin, xmax, ymax = bp.bounds
        if name == "i" and ymin > 500:  # the tittle: replace with a circle
            dots.append((x + (xmin + xmax) / 2, (ymin + ymax) / 2, max(xmax - xmin, ymax - ymin) / 2)); continue
        sp = SVGPathPen(gs); tp = TransformPen(sp, (1, 0, 0, -1, x, 0))
        for op, args in c: getattr(tp, op)(*args)
        ink.append(sp.getCommands())
    x += pos.x_advance + track * upm + (60 if name == "r" else 0)  # keep the r off the o
x -= track * upm
os2 = f["OS/2"]
print(json.dumps({"upm": upm, "advance": x, "xHeight": os2.sxHeight, "cap": os2.sCapHeight,
  "asc": f["hhea"].ascent, "desc": f["hhea"].descent, "d": " ".join(ink), "dots": dots}))
