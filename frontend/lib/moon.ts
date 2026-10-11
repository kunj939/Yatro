/** Where the real moon is and how much of it is lit, for the dark theme's sky (see components/LiveMoon.tsx).
 *
 * Low-precision formulas from Jean Meeus, "Astronomical Algorithms", in the compact form used by SunCalc
 * (V. Agafonkin): good to about a degree of position and a few percent of illumination, far more than a drawing
 * needs. Angles are radians inside, degrees at the edges. */

const RAD = Math.PI / 180;
const DAY_MS = 86400000, J1970 = 2440588, J2000 = 2451545;
const OBLIQUITY = RAD * 23.4397;
const SUN_KM = 149598000;

const toDays = (date: Date) => date.valueOf() / DAY_MS - 0.5 + J1970 - J2000;
const rightAscension = (l: number, b: number) =>
  Math.atan2(Math.sin(l) * Math.cos(OBLIQUITY) - Math.tan(b) * Math.sin(OBLIQUITY), Math.cos(l));
const declination = (l: number, b: number) =>
  Math.asin(Math.sin(b) * Math.cos(OBLIQUITY) + Math.cos(b) * Math.sin(OBLIQUITY) * Math.sin(l));
const siderealTime = (d: number, lw: number) => RAD * (280.16 + 360.9856235 * d) - lw;

function sunCoords(d: number) {
  const M = RAD * (357.5291 + 0.98560028 * d);
  const C = RAD * (1.9148 * Math.sin(M) + 0.02 * Math.sin(2 * M) + 0.0003 * Math.sin(3 * M));
  const L = M + C + RAD * 102.9372 + Math.PI;
  return { ra: rightAscension(L, 0), dec: declination(L, 0) };
}

function moonCoords(d: number) {
  const L = RAD * (218.316 + 13.176396 * d); // ecliptic longitude
  const M = RAD * (134.963 + 13.064993 * d); // mean anomaly
  const F = RAD * (93.272 + 13.22935 * d); // mean distance
  const l = L + RAD * 6.289 * Math.sin(M);
  const b = RAD * 5.128 * Math.sin(F);
  return { ra: rightAscension(l, b), dec: declination(l, b), dist: 385001 - 20905 * Math.cos(M) };
}

export interface MoonState {
  altitude: number; // degrees above the horizon; zero or less means it hasn't risen or has set
  azimuth: number; // compass degrees: 90 east, 180 south, 270 west
  fraction: number; // lit part: 0 new moon .. 1 full moon
  brightLimb: number; // which way the lit edge points as you'd see it: degrees anticlockwise from straight up
  waxing: boolean;
}

export function moonState(date: Date, lat: number, lng: number): MoonState {
  const d = toDays(date), phi = RAD * lat, lw = RAD * -lng;
  const m = moonCoords(d), s = sunCoords(d);
  const H = siderealTime(d, lw) - m.ra;
  let h = Math.asin(Math.sin(phi) * Math.sin(m.dec) + Math.cos(phi) * Math.cos(m.dec) * Math.cos(H));
  const h0 = Math.max(h, 0);
  h += 0.0002967 / Math.tan(h0 + 0.00312536 / (h0 + 0.08901179)); // refraction lifts it a little near the horizon
  const az = Math.atan2(Math.sin(H), Math.cos(H) * Math.sin(phi) - Math.tan(m.dec) * Math.cos(phi)); // from south
  const parallactic = Math.atan2(Math.sin(H), Math.tan(phi) * Math.cos(m.dec) - Math.sin(m.dec) * Math.cos(H));
  const elong = Math.acos(Math.sin(s.dec) * Math.sin(m.dec) + Math.cos(s.dec) * Math.cos(m.dec) * Math.cos(s.ra - m.ra));
  const inc = Math.atan2(SUN_KM * Math.sin(elong), m.dist - SUN_KM * Math.cos(elong));
  const limb = Math.atan2(Math.cos(s.dec) * Math.sin(s.ra - m.ra),
                          Math.sin(s.dec) * Math.cos(m.dec) - Math.cos(s.dec) * Math.sin(m.dec) * Math.cos(s.ra - m.ra));
  return {
    altitude: h / RAD,
    azimuth: (((az / RAD + 180) % 360) + 360) % 360,
    fraction: (1 + Math.cos(inc)) / 2,
    brightLimb: (limb - parallactic) / RAD,
    waxing: limb < 0,
  };
}

/** The lit part of a moon of radius `r` centred on (0, 0), lit from the right. Rotate it to point the lit edge the
 * right way (see MoonState.brightLimb). */
export function litPath(fraction: number, r: number): string {
  const k = Math.min(1, Math.max(0, fraction));
  const rx = Math.abs(1 - 2 * k) * r; // the shadow line is half an ellipse this wide
  const sweep = k > 0.5 ? 1 : 0; // a gibbous moon's shadow line bulges away from the lit side, a crescent's towards it
  return `M 0 ${-r} A ${r} ${r} 0 0 1 0 ${r} A ${rx.toFixed(2)} ${r} 0 0 ${sweep} 0 ${-r} Z`;
}

// Where the visitor probably is, from their time zone: no location prompt for a decoration. Close enough, since a
// few hundred km moves moonrise by minutes.
const PLACES: Record<string, [number, number]> = {
  "Asia/Kolkata": [23.0, 78.0], "Asia/Calcutta": [23.0, 78.0], "Asia/Dubai": [25.2, 55.3], "Asia/Singapore": [1.35, 103.8],
  "Asia/Bangkok": [13.75, 100.5], "Asia/Tokyo": [35.7, 139.7], "Asia/Shanghai": [31.2, 121.5], "Asia/Kathmandu": [27.7, 85.3],
  "Asia/Dhaka": [23.8, 90.4], "Asia/Karachi": [24.9, 67.0], "Asia/Colombo": [6.9, 79.9], "Asia/Jakarta": [-6.2, 106.8],
  "Asia/Makassar": [-8.65, 115.2], "Asia/Kuala_Lumpur": [3.15, 101.7], "Europe/London": [51.5, -0.1],
  "Europe/Paris": [48.9, 2.35], "Europe/Berlin": [52.5, 13.4], "Europe/Rome": [41.9, 12.5], "Europe/Madrid": [40.4, -3.7],
  "Europe/Istanbul": [41.0, 29.0], "America/New_York": [40.7, -74.0], "America/Chicago": [41.9, -87.6],
  "America/Los_Angeles": [34.05, -118.2], "America/Toronto": [43.7, -79.4], "America/Vancouver": [49.3, -123.1],
  "America/Sao_Paulo": [-23.5, -46.6], "Australia/Sydney": [-33.9, 151.2], "Australia/Melbourne": [-37.8, 145.0],
  "Africa/Johannesburg": [-26.2, 28.0], "Pacific/Auckland": [-36.85, 174.8],
};
const SOUTHERN = /^(Australia|Antarctica)\/|^Pacific\/(Auckland|Fiji)|^America\/(Sao_Paulo|Argentina|Santiago|Lima|Montevideo)|^Africa\/(Johannesburg|Maputo|Harare|Windhoek)/;

export function guessLocation(): { lat: number; lng: number } {
  let tz = "";
  try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch { /* very old browser */ }
  const known = PLACES[tz];
  if (known) return { lat: known[0], lng: known[1] };
  return { lat: SOUTHERN.test(tz) ? -30 : 30, lng: (-new Date().getTimezoneOffset() / 60) * 15 };
}
