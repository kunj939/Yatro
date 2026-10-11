import { ImageResponse } from "next/og";
import { dateSpan, dayDate, fetchShared, shortMoney } from "@/lib/shareCard";
import type { SharedTrip } from "@/lib/types";

// The picture WhatsApp (and Telegram, iMessage, Slack…) shows when a share link is sent: the trip as a card, in the
// site's own dark-and-lime look, so the chat gets something you'd tap rather than a wall of text.
export const alt = "A trip planned with Yatro";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

const INK = "#0b0d0c";
const LIME = "#beff4a";
const TONES = ["#beff4a", "#2dd4e6", "#ff8fbe", "#ffc46e", "#c4a7ff"];

/** Google Fonts serves TTF to a request without a browser user agent, which is what the image renderer can read. */
async function font(family: string, query: string, text: string): Promise<ArrayBuffer | null> {
  try {
    const css = await (await fetch(`https://fonts.googleapis.com/css2?family=${family}:${query}&text=${encodeURIComponent(text)}`,
      { signal: AbortSignal.timeout(4000) })).text();
    const url = css.match(/src: url\((.+?)\) format\('(opentype|truetype)'\)/)?.[1];
    return url ? await (await fetch(url, { signal: AbortSignal.timeout(4000) })).arrayBuffer() : null;
  } catch {
    return null;
  }
}

function clip(s: string, n: number): string {
  return s.length <= n ? s : `${s.slice(0, n - 1).trimEnd()}…`;
}

function Chip({ children, tone = "rgba(255,255,255,0.10)", color = "rgba(255,255,255,0.88)" }:
  { children: React.ReactNode; tone?: string; color?: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", padding: "10px 20px", borderRadius: 999, background: tone, color, fontSize: 24 }}>
      {children}
    </div>
  );
}

function Brand() {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
      <div style={{ display: "flex", width: 46, height: 46, borderRadius: 12, background: "#151a17", alignItems: "center",
        justifyContent: "center", color: LIME, fontSize: 32, fontFamily: "Head" }}>Y</div>
      <div style={{ display: "flex", fontFamily: "Serif", fontStyle: "italic", fontSize: 40, color: "white" }}>Yatro</div>
    </div>
  );
}

function TripCard({ v }: { v: SharedTrip }) {
  const cur = v.trip.currency;
  const days = v.itinerary.days;
  const shown = days.length > 5 ? days.slice(0, 4) : days;
  const stops = days.reduce((n, d) => n + d.items.filter((i) => i.kind !== "free").length, 0);
  const total = v.itinerary.total_cost;
  return (
    <div style={{ display: "flex", flexDirection: "column", width: "100%", height: "100%" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <Brand />
        <Chip tone="rgba(190,255,74,0.14)" color={LIME}>Trip plan · {days.length} day{days.length === 1 ? "" : "s"}</Chip>
      </div>
      <div style={{ display: "flex", flexDirection: "column", marginTop: 34 }}>
        <div style={{ display: "flex", fontFamily: "Head", fontSize: 60, lineHeight: 1.05, color: "white", letterSpacing: -1.5 }}>
          {clip(v.itinerary.title || v.trip.title, 62)}
        </div>
        <div style={{ display: "flex", gap: 12, marginTop: 24 }}>
          <Chip>{dateSpan(v.trip)}</Chip>
          <Chip>{v.trip.travellers} traveller{v.trip.travellers === 1 ? "" : "s"}</Chip>
          <Chip>{stops} stops</Chip>
          {total ? <Chip tone={LIME} color={INK}>{shortMoney(total, cur)}</Chip> : null}
        </div>
      </div>
      <div style={{ display: "flex", gap: 14, marginTop: "auto" }}>
        {shown.map((d, n) => {
          const items = d.items.filter((i) => i.kind !== "free");
          const cost = d.items.reduce((s, i) => s + (i.cost || 0), 0);
          return (
            <div key={d.index} style={{ display: "flex", flexDirection: "column", flex: 1, padding: "18px 20px", borderRadius: 20,
              background: "#141917", borderTop: `5px solid ${TONES[n % TONES.length]}` }}>
              <div style={{ display: "flex", fontSize: 18, letterSpacing: 2, color: TONES[n % TONES.length] }}>{d.label.toUpperCase()}</div>
              <div style={{ display: "flex", fontSize: 26, color: "white", marginTop: 6 }}>{dayDate(d.date)}</div>
              <div style={{ display: "flex", fontSize: 20, color: "rgba(255,255,255,0.55)", marginTop: 4 }}>
                {items.length} stop{items.length === 1 ? "" : "s"}{cost ? ` · ${shortMoney(cost, cur)}` : ""}
              </div>
            </div>
          );
        })}
        {days.length > 5 && (
          <div style={{ display: "flex", flexDirection: "column", justifyContent: "center", alignItems: "center", flex: 1,
            borderRadius: 20, background: "#141917", color: "rgba(255,255,255,0.75)", fontSize: 26 }}>
            +{days.length - 4} more days
          </div>
        )}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 22, fontSize: 22,
        color: "rgba(255,255,255,0.6)" }}>
        <div style={{ display: "flex" }}>Tap to open the full day-by-day plan</div>
        {total && v.trip.travellers > 1
          ? <div style={{ display: "flex", color: "white" }}>{shortMoney(v.itinerary.cost_per_person, cur)} each</div> : null}
      </div>
    </div>
  );
}

function Fallback() {
  return (
    <div style={{ display: "flex", flexDirection: "column", width: "100%", height: "100%" }}>
      <Brand />
      <div style={{ display: "flex", flexDirection: "column", marginTop: "auto", marginBottom: "auto" }}>
        <div style={{ display: "flex", fontFamily: "Head", fontSize: 76, color: "white", letterSpacing: -2 }}>A trip planned with</div>
        <div style={{ display: "flex", fontFamily: "Serif", fontStyle: "italic", fontSize: 96, color: LIME }}>Yatro.</div>
        <div style={{ display: "flex", fontSize: 30, color: "rgba(255,255,255,0.7)", marginTop: 18 }}>
          Travel, stays, sights and meals that fit the budget. Tap to open the plan.
        </div>
      </div>
    </div>
  );
}

export default async function Image({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  const v = await fetchShared(token);
  const words = v ? JSON.stringify(v.itinerary.days.map((d) => d.label)) + (v.itinerary.title || v.trip.title) : "";
  const text = `Yatro A trip planned with Trip plan days stops travellers each more Tap to open the full day-by-day plan ${words} 0123456789 ·–…+,.:₹€$£`;
  const [head, body, serif, money] = await Promise.all([
    font("Bricolage+Grotesque", "wght@800", text),
    font("Plus+Jakarta+Sans", "wght@600", text + "JanFebMarAprMayJunJulAugSepOctNovDecMonTueWedThuFriSatSun"),
    font("Instrument+Serif", "ital@1", "Yatro."),
    font("Noto+Sans", "wght@600", "₹€$£"),
  ]);
  const fonts = [
    head && { name: "Head", data: head, weight: 800 as const, style: "normal" as const },
    body && { name: "Body", data: body, weight: 600 as const, style: "normal" as const },
    serif && { name: "Serif", data: serif, weight: 400 as const, style: "italic" as const },
    money && { name: "Money", data: money, weight: 600 as const, style: "normal" as const },
  ].filter(Boolean) as { name: string; data: ArrayBuffer; weight: 400 | 600 | 800; style: "normal" | "italic" }[];

  return new ImageResponse(
    (
      <div style={{ display: "flex", flexDirection: "column", width: "100%", height: "100%", padding: "48px 56px", background: INK,
        backgroundImage: "radial-gradient(circle at 88% 8%, rgba(190,255,74,0.22), transparent 42%), radial-gradient(circle at 6% 100%, rgba(45,212,230,0.16), transparent 45%)",
        fontFamily: "Body, Money", color: "white" }}>
        {v ? <TripCard v={v} /> : <Fallback />}
      </div>
    ),
    { ...size, fonts },
  );
}
