import { createElement } from "react";
import { endpoints } from "./api";

/** The trip as a PDF file, made in the browser from the public share view (what the link shows, nothing more).
 * The PDF library and the document are loaded only here, when someone asks for a PDF. */
export async function buildTripPdf(token: string, url: string): Promise<File> {
  const [{ pdf }, { TripPdf }, view] = await Promise.all([
    import("@react-pdf/renderer"), import("@/components/trip/TripPdf"), endpoints.shared(token),
  ]);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const blob = await pdf(createElement(TripPdf, { view, url }) as any).toBlob();
  const name = (view.itinerary.title || view.trip.title).replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/g, "").slice(0, 60) || "trip";
  return new File([blob], `${name}-Yaitro.pdf`, { type: "application/pdf" });
}

/** Hand the PDF to the phone's share menu (pick WhatsApp and it goes as a file). Where a browser can't share files,
 * the PDF is saved instead, to attach by hand. Returns what happened. */
export async function sendPdf(file: File, title: string, url: string): Promise<"shared" | "cancelled" | "saved"> {
  if (typeof navigator !== "undefined" && navigator.canShare?.({ files: [file] })) {
    try {
      await navigator.share({ files: [file], title, text: `${title}\n${url}` });
      return "shared";
    } catch (e) {
      if ((e as DOMException)?.name === "AbortError") return "cancelled";
    }
  }
  savePdf(file);
  return "saved";
}

/** Save the PDF to the device's downloads. */
export function savePdf(file: File): void {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(file);
  a.download = file.name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 30_000);
}
