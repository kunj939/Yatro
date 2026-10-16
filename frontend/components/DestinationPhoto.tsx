"use client";

import { useEffect, useState } from "react";
import { MapPin } from "lucide-react";
import { endpoints } from "@/lib/api";
import { DESTINATION_PHOTOS } from "@/lib/photos";

type Shot = { url: string; credit?: string; license?: string | null; source_url?: string | null; alt: string; local: boolean };

// One lookup per destination per visit, shared by every card that shows it (a page of trip cards asks once each).
const memo = new Map<string, Promise<Shot | null>>();

/** Our own photo for the eight cities we have (lib/photos.ts), otherwise one from Wikimedia Commons via the API. */
export function findPhoto(name: string, lat?: number | null, lng?: number | null): Promise<Shot | null> {
  const city = (name || "").split(",")[0].trim();
  const own = DESTINATION_PHOTOS.find((d) => d.city.toLowerCase() === city.toLowerCase());
  if (own) return Promise.resolve({ url: own.src, alt: own.alt, local: true });
  if (city.length < 2) return Promise.resolve(null);
  const key = `${city.toLowerCase()}|${lat?.toFixed(1) ?? ""}|${lng?.toFixed(1) ?? ""}`;
  if (!memo.has(key)) {
    memo.set(key, endpoints.placePhoto(city, lat, lng)
      .then((r) => (r.photo ? { ...r.photo, alt: `A view of ${city}`, local: false } : null))
      .catch(() => null));
  }
  return memo.get(key)!;
}

/** A destination's photo, filling its box. While it loads, or when there's none, a soft brand gradient with a pin keeps
 *  the card looking finished. Wikimedia photos carry their credit (author and licence), as their licences ask. */
export function DestinationPhoto({ name, lat, lng, className = "", credit = true, children }: {
  name: string; lat?: number | null; lng?: number | null; className?: string; credit?: boolean; children?: React.ReactNode;
}) {
  const [shot, setShot] = useState<Shot | null>(null);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    let live = true;
    setLoaded(false);
    findPhoto(name, lat, lng).then((s) => live && setShot(s));
    return () => { live = false; };
  }, [name, lat, lng]);
  return (
    <div className={`relative isolate overflow-hidden bg-gradient-to-br from-signal-soft via-paper to-teal-soft ${className}`}>
      <div aria-hidden className="absolute inset-0 -z-10 opacity-50 [background-image:radial-gradient(rgb(var(--signal)/0.18)_1px,transparent_1px)] [background-size:14px_14px]" />
      {!shot && <MapPin aria-hidden className="absolute left-1/2 top-1/2 h-8 w-8 -translate-x-1/2 -translate-y-1/2 text-signal/40" />}
      {shot && (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={shot.url} alt={shot.alt} loading="lazy" decoding="async" onLoad={() => setLoaded(true)} onError={() => setShot(null)}
             className={`absolute inset-0 h-full w-full object-cover transition-[opacity,transform] duration-700 [filter:saturate(1.1)_contrast(1.04)] ${loaded ? "scale-100 opacity-100" : "scale-[1.03] opacity-0"}`} />
      )}
      {children}
      {credit && shot && !shot.local && loaded && (
        <a href={shot.source_url || "https://commons.wikimedia.org"} target="_blank" rel="noopener noreferrer"
           className="absolute bottom-1.5 right-2 z-10 max-w-[70%] truncate rounded-full bg-black/35 px-2 py-0.5 text-[10px] text-white/90 backdrop-blur-sm hover:bg-black/55"
           title={`Photo: ${shot.credit}${shot.license ? `, ${shot.license}` : ""}, via Wikimedia Commons`}>
          Photo: {shot.credit}{shot.license ? ` · ${shot.license}` : ""}
        </a>
      )}
    </div>
  );
}
