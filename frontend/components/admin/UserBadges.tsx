import type { AdminUser } from "@/lib/admin";
import { Pill } from "./ui";

export function UserBadges({ u }: { u: Pick<AdminUser, "is_owner" | "is_admin" | "is_active" | "email_verified" | "phone_verified"> }) {
  return (
    <>
      {u.is_owner && <Pill tone="brand">Owner</Pill>}
      {u.is_admin && !u.is_owner && <Pill tone="brand">Admin</Pill>}
      {!u.is_active && <Pill tone="bad">Suspended</Pill>}
      {!u.email_verified && <Pill tone="warn">Email not verified</Pill>}
    </>
  );
}
