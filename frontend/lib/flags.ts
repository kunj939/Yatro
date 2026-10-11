"use client";

import { useEffect, useState } from "react";
import { endpoints } from "./api";

export interface Flags { share: boolean; upi: boolean; planEmails: boolean }
let pending: Promise<Flags> | null = null;

/** Which optional features the server has switched on (SHARE_LINKS_ENABLED, UPI_LINKS_ENABLED). On until told otherwise. */
export function useFlags(): Flags {
  const [flags, setFlags] = useState<Flags>({ share: true, upi: true, planEmails: false });
  useEffect(() => {
    pending ??= endpoints.config().then((c) => ({ share: c.share_links_enabled !== false, upi: c.upi_links_enabled !== false, planEmails: c.plan_emails === true }))
      .catch(() => ({ share: true, upi: true, planEmails: false }));
    pending.then(setFlags);
  }, []);
  return flags;
}
