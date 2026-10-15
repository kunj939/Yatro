"use client";

// The plan as a PDF, in the site's dark-and-lime look: a cover with the trip at a glance, then a page per day.
// Built in the browser from the public share view (exactly what the share link shows) and loaded only when someone
// asks for it (lib/tripPdf.ts), so the library never weighs on the normal pages.
import { Document, Font, Image, Link, Page, StyleSheet, Text, View } from "@react-pdf/renderer";
import type { SharedItem, SharedTrip } from "@/lib/types";
import type { PdfMaps } from "@/lib/pdfMaps";
import { dateSpan, dayDate, shortMoney } from "@/lib/shareCard";

let fontsReady = false;
function registerFonts() {
  if (fontsReady) return;
  const origin = window.location.origin;
  Font.register({ family: "Noto", fonts: [
    { src: `${origin}/fonts/NotoSans-Regular.ttf`, fontWeight: 400 },
    { src: `${origin}/fonts/NotoSans-Bold.ttf`, fontWeight: 700 },
  ] });
  Font.register({ family: "Head", src: `${origin}/fonts/Bricolage-ExtraBold.ttf`, fontWeight: 800 });
  Font.registerHyphenationCallback((w) => [w]);  // keep place names whole
  fontsReady = true;
}

const INK = "#0b0d0c", CARD = "#141917", LINE = "#232a26", LIME = "#beff4a";
const MUTE = "#9aa39d", SOFT = "#cdd3cf";
const TONES = ["#beff4a", "#2dd4e6", "#ff8fbe", "#ffc46e", "#c4a7ff"];
const KIND: Record<string, { label: string; color: string }> = {
  transport: { label: "TRAVEL", color: "#2dd4e6" }, activity: { label: "VISIT", color: "#beff4a" },
  meal: { label: "MEAL", color: "#ffc46e" }, sunset: { label: "SUNSET", color: "#ff8fbe" },
  stay: { label: "STAY", color: "#c4a7ff" }, arrival: { label: "ARRIVE", color: "#9aa39d" },
};

const s = StyleSheet.create({
  page: { backgroundColor: INK, color: "white", fontFamily: "Noto", fontSize: 10, padding: 34, paddingBottom: 50 },
  brand: { flexDirection: "row", alignItems: "center", gap: 8 },
  logo: { width: 24, height: 24, borderRadius: 6, backgroundColor: CARD, color: LIME, fontFamily: "Head", fontSize: 15,
    textAlign: "center", paddingTop: 2 },
  title: { fontFamily: "Head", fontSize: 30, lineHeight: 1.1, marginTop: 26 },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 14 },
  chip: { paddingVertical: 5, paddingHorizontal: 11, borderRadius: 999, backgroundColor: "#1d2320", color: SOFT, fontSize: 10 },
  pin: { width: 13, height: 13, borderRadius: 7, backgroundColor: "#659600", color: "white", fontSize: 7.5, fontWeight: 700,
    textAlign: "center", paddingTop: 1.5 },
  stat: { flex: 1, backgroundColor: CARD, borderRadius: 10, padding: 12, borderWidth: 1, borderColor: LINE },
  statLabel: { color: MUTE, fontSize: 8, letterSpacing: 1 },
  statValue: { fontSize: 16, fontWeight: 700, marginTop: 4 },
  h2: { fontFamily: "Head", fontSize: 15, marginTop: 24, marginBottom: 10 },
  dayHead: { flexDirection: "row", justifyContent: "space-between", alignItems: "flex-end", paddingBottom: 10,
    borderBottomWidth: 1, borderBottomColor: LINE, marginBottom: 6 },
  row: { flexDirection: "row", alignItems: "flex-start", paddingVertical: 7, borderBottomWidth: 1, borderBottomColor: "#171c19" },
  time: { width: 44, color: LIME, fontWeight: 700, fontSize: 10 },
  badge: { width: 50, fontSize: 7, fontWeight: 700, letterSpacing: 0.8, paddingTop: 2 },
  footer: { position: "absolute", bottom: 20, left: 34, right: 34, flexDirection: "row", justifyContent: "space-between",
    color: MUTE, fontSize: 8 },
});

function Footer({ url }: { url: string }) {
  return (
    <View style={s.footer} fixed>
      <Text>Planned with <Text style={{ color: LIME }}>Yaitro</Text> · <Link src={url} style={{ color: SOFT, textDecoration: "none" }}>{url.replace(/^https?:\/\//, "")}</Link></Text>
      <Text render={({ pageNumber, totalPages }) => `${pageNumber} / ${totalPages}`} />
    </View>
  );
}

function Brand() {
  return (
    <View style={s.brand}>
      <Text style={s.logo}>Y</Text>
      <Text style={{ fontFamily: "Head", fontSize: 16 }}>Yaitro</Text>
    </View>
  );
}

const shown = (items: SharedItem[]) => items.filter((i) => i.kind !== "free");
const dayCost = (items: SharedItem[]) => items.reduce((n, i) => n + (i.cost || 0), 0);

const PIN = "123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ";

function ItemRow({ i, cur, pin, place }: { i: SharedItem; cur: string; pin?: number; place?: string }) {
  const k = KIND[i.kind] ?? { label: i.kind.toUpperCase(), color: MUTE };
  const estimated = i.cost && i.cost_state !== "verified_official" && i.cost_state !== "verified_provider";
  return (
    <View style={s.row} wrap={false}>
      <Text style={s.time}>{i.time || (i.meta?.when as string) || ""}</Text>
      <Text style={[s.badge, { color: k.color }]}>{k.label}</Text>
      <View style={{ flex: 1, paddingRight: 8 }}>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 5 }}>
          {pin ? <Text style={s.pin}>{PIN[pin - 1] ?? ""}</Text> : null}
          {place ? (  // the stop's name opens it in Google Maps
            <Link src={place} style={{ fontWeight: 700, fontSize: 10.5, flex: 1, color: "white", textDecoration: "none" }}>{i.title}</Link>
          ) : <Text style={{ fontWeight: 700, fontSize: 10.5, flex: 1 }}>{i.title}</Text>}
        </View>
        {i.subtitle ? <Text style={{ color: MUTE, fontSize: 8.5, marginTop: 2 }}>{i.subtitle}</Text> : null}
      </View>
      <Text style={{ width: 70, textAlign: "right", fontWeight: 700, color: i.cost ? "white" : "#5fe3c5" }}>
        {i.cost ? `${shortMoney(i.cost, cur)}${estimated ? "*" : ""}` : i.kind === "activity" ? "Free" : ""}
      </Text>
    </View>
  );
}

export function TripPdf({ view, url, maps }: { view: SharedTrip; url: string; maps?: PdfMaps }) {
  registerFonts();
  const { trip, itinerary: it } = view;
  const cur = trip.currency;
  const stops = it.days.reduce((n, d) => n + shown(d.items).length, 0);
  const anyEstimate = it.days.some((d) => d.items.some((i) => i.cost && i.cost_state !== "verified_official" && i.cost_state !== "verified_provider"));
  return (
    <Document title={it.title || trip.title} author="Yaitro" subject="Trip plan" creator="Yaitro">
      <Page size="A4" style={s.page}>
        <View style={{ position: "absolute", top: -120, right: -120, width: 340, height: 340, borderRadius: 170, backgroundColor: "#1a2a0d" }} fixed />
        <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
          <Brand />
          <Text style={[s.chip, { backgroundColor: "#24331a", color: LIME }]}>Trip plan · {it.days.length} day{it.days.length === 1 ? "" : "s"}</Text>
        </View>
        <Text style={s.title}>{it.title || trip.title}</Text>
        <View style={s.chips}>
          <Text style={s.chip}>{dateSpan(trip)}</Text>
          <Text style={s.chip}>{trip.travellers} traveller{trip.travellers === 1 ? "" : "s"}</Text>
          <Text style={s.chip}>{stops} stops</Text>
          <Text style={s.chip}>{trip.destination}</Text>
        </View>

        <View style={{ flexDirection: "row", gap: 8, marginTop: 18 }}>
          <View style={[s.stat, { borderColor: "#3a5a12" }]}>
            <Text style={s.statLabel}>PLANNED COST</Text>
            <Text style={[s.statValue, { color: LIME }]}>{it.total_cost ? shortMoney(it.total_cost, cur) : "—"}</Text>
          </View>
          {trip.travellers > 1 && (
            <View style={s.stat}>
              <Text style={s.statLabel}>PER PERSON</Text>
              <Text style={s.statValue}>{it.total_cost ? shortMoney(it.cost_per_person, cur) : "—"}</Text>
            </View>
          )}
          {view.budget && (
            <View style={s.stat}>
              <Text style={s.statLabel}>BUDGET · LEFT</Text>
              <Text style={s.statValue}>{shortMoney(view.budget.budget, cur)}<Text style={{ color: "#5fe3c5", fontSize: 11 }}>  {shortMoney(view.budget.remaining, cur)} left</Text></Text>
            </View>
          )}
        </View>

        <Text style={s.h2}>Your days</Text>
        <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
          {it.days.map((d, n) => (
            <View key={d.index} style={{ width: "31.8%", backgroundColor: CARD, borderRadius: 10, padding: 10, borderTopWidth: 3,
              borderTopColor: TONES[n % TONES.length] }} wrap={false}>
              <Text style={{ color: TONES[n % TONES.length], fontSize: 8, fontWeight: 700, letterSpacing: 1 }}>{d.label.toUpperCase()}</Text>
              <Text style={{ fontWeight: 700, fontSize: 11, marginTop: 3 }}>{dayDate(d.date)}</Text>
              <Text style={{ color: MUTE, fontSize: 8.5, marginTop: 2 }}>
                {shown(d.items).length} stop{shown(d.items).length === 1 ? "" : "s"}{dayCost(d.items) ? ` · ${shortMoney(dayCost(d.items), cur)}` : ""}
              </Text>
            </View>
          ))}
        </View>

        {it.summary ? (<><Text style={s.h2}>The plan</Text><Text style={{ color: SOFT, lineHeight: 1.35 }}>{it.summary}</Text></>) : null}
        <Footer url={url} />
      </Page>

      <Page size="A4" style={s.page}>
        {it.days.map((d, n) => {
          const tone = TONES[n % TONES.length];
          const count = shown(d.items).length;
          return (
            <View key={d.index} style={{ marginBottom: 22 }}>
              {/* the header never sits alone at the bottom of a page: it moves over with the first stops */}
              <View wrap={false} minPresenceAhead={140}>
                <View style={s.dayHead}>
                  <View>
                    <Text style={{ color: tone, fontSize: 9, fontWeight: 700, letterSpacing: 1.2 }}>{d.label.toUpperCase()}</Text>
                    <Text style={{ fontFamily: "Head", fontSize: 20, marginTop: 2 }}>{dayDate(d.date)}</Text>
                  </View>
                  <View style={{ alignItems: "flex-end" }}>
                    <Text style={{ color: MUTE, fontSize: 9 }}>{count} stop{count === 1 ? "" : "s"}</Text>
                    {dayCost(d.items) ? <Text style={{ fontWeight: 700, fontSize: 13, color: tone, marginTop: 2 }}>{shortMoney(dayCost(d.items), cur)}</Text> : null}
                  </View>
                </View>
                <View style={{ height: 3, backgroundColor: tone, borderRadius: 2, marginBottom: 6, width: 60 }} />
                {maps?.routes[d.index] ? (
                  <Link src={maps.routes[d.index]} style={{ color: tone, fontSize: 9, fontWeight: 700, textDecoration: "none", marginBottom: 6 }}>
                    Open this day's route in Google Maps
                  </Link>
                ) : null}
              </View>
              {/* the day's stops on a map, numbered as in the list below (Google Static Maps, lib/pdfMaps.ts) */}
              {maps?.maps[d.index] ? (
                <View wrap={false} style={{ marginBottom: 8 }}>
                  <Image src={maps.maps[d.index]} style={{ width: "100%", height: 245, borderRadius: 8, objectFit: "cover" }} />
                </View>
              ) : null}
              {shown(d.items).map((i) => <ItemRow key={i.id} i={i} cur={cur} pin={maps?.numbers[i.id]} place={maps?.places[i.id]} />)}
            </View>
          );
        })}
        {anyEstimate || view.google.attribution ? (
          <View style={{ marginTop: 4 }} wrap={false}>
            {anyEstimate ? <Text style={{ color: MUTE, fontSize: 8 }}>* Estimated price. Check before you book.</Text> : null}
            {view.google.attribution ? <Text style={{ color: MUTE, fontSize: 8, marginTop: 2 }}>{view.google.attribution}</Text> : null}
          </View>
        ) : null}
        <Footer url={url} />
      </Page>
    </Document>
  );
}
