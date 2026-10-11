/** UPI payment links (ADR 0006). Yatro never touches the money: this only builds a link that opens the person's own UPI
 *  app with the payee, amount and note filled in. A payee's UPI ID typed here lives in the browser only. */
export const UPI_RE = /^[a-zA-Z0-9.\-_]{2,256}@[a-zA-Z]{2,64}$/;

export const validUpi = (v: string) => UPI_RE.test(v.trim());

export function upiLink({ payee, name, amount, note }: { payee: string; name: string; amount: number; note: string }): string {
  const q = [["pa", payee.trim()], ["pn", name], ["am", amount.toFixed(2)], ["cu", "INR"], ["tn", note]]
    .map(([k, v]) => `${k}=${k === "pa" ? encodeURIComponent(v).replace(/%40/g, "@") : encodeURIComponent(v)}`).join("&"); // "@" stays literal in the payee, as UPI apps expect
  return `upi://pay?${q}`;
}

/** UPI links only open an app on a phone. */
export function onMobile(): boolean {
  return typeof navigator !== "undefined" && /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
}
