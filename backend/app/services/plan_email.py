"""The plan, by email: sent to the person's own verified address when a NEW plan is ready, and on demand ("Email me this plan").

* Built from the same allow-listed view as the share page and the WhatsApp text (share_service.share_view), so every train, fare and Google
  fact goes through the same gates (I1, I6) and nothing private about anyone else can get in (I8). Made at the moment of sending; stored nowhere.
* Goes only to the account's own email, only if they have not switched plan emails off (Account page) and the admin switch `plan_emails`
  is on. It never delays or fails a plan: it runs after the plan is saved, in the background, and every failure is just logged (I3).
* The "Open your plan" button points at the private trip page (it needs sign-in). The WhatsApp button is a wa.me link to the person's OWN saved
  number with the message typed in; they tap Send, Yaitro sends nothing (I9).
"""
from __future__ import annotations

import html as H
import logging
import re
import threading
from datetime import date
from types import SimpleNamespace

from app.core import killswitch
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.user import User
from app.services import mailer, share_service
from app.services import trip_service as TS
from app.utils.money import fmt_money

log = logging.getLogger("tripai.plan_email")

LIME, NIGHT, INK, MUTE, LINE, PAPER = "#bcff4a", "#0f1219", "#171a21", "#667085", "#e6e8ee", "#ffffff"
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
EMAIL_TEXT_LIMIT = 6000


class NotReady(Exception):
    """The trip has no plan yet, or the account cannot be emailed."""


def trip_url(trip) -> str:
    return f"{get_settings().public_app_url.rstrip('/')}/trips/{trip.id}"


def _view(trip) -> dict:
    # the owner's own email: their budget is theirs to see; the split and members are not included
    return share_service.share_view(trip, SimpleNamespace(show_budget=True, include_split=False))


def _plain(text: str) -> str:
    """WhatsApp markup (*bold*, _italic_) removed for the plain-text part of the email."""
    text = re.sub(r"(?m)^_(.*)_$", r"\1", text)
    return text.replace("*", "")


def _row(i: dict, cur: str) -> str:
    meta = i.get("meta") or {}
    sub = i.get("subtitle") if i["kind"] == "transport" else (f"Open {i['opening_time']}–{i['closing_time']}" if i.get("opening_time") else i.get("subtitle"))
    if i.get("travel_minutes_from_prev"):
        hop = f"{i['travel_minutes_from_prev']} min {(i.get('travel_mode') or 'travel').replace('_', ' ')} from the last stop"
        sub = f"{sub} · {hop}" if sub else hop
    when = i.get("time") or meta.get("when") or ""
    cost = ""
    if i.get("cost"):
        tag = " (sample)" if i.get("cost_state") == "demo" else (" (est.)" if share_service._estimated(i) else "")
        cost = f"{fmt_money(i['cost'], cur)}<span style=\"font-weight:400;color:{MUTE};font-size:12px\">{H.escape(tag)}</span>"
    subtitle = f'<div style="color:{MUTE};font-size:13px;line-height:1.4;margin-top:2px">{H.escape(sub)}</div>' if sub else ""
    price = f'<div style="font:700 14px/1.4 {FONT};color:{INK};margin-top:4px">{cost}</div>' if cost else ""
    # three columns (icon, time, everything else) so a phone gives the title the whole width; the cost sits under it
    return (f'<tr><td width="34" valign="top" style="padding:12px 0 12px 12px;font-size:20px;line-height:24px">{share_service.item_emoji(i)}</td>'
            f'<td width="50" valign="top" style="padding:12px 6px;font:700 14px/24px {FONT};color:{INK}">{H.escape(when)}</td>'
            f'<td valign="top" style="padding:12px 12px 12px 0;font:600 15px/24px {FONT};color:{INK}">{H.escape(i["title"])}{subtitle}{price}</td></tr>'
            f'<tr><td colspan="3" style="border-top:1px solid {LINE};font-size:0;line-height:0">&nbsp;</td></tr>')

def _button(href: str, label: str, bg: str, fg: str) -> str:
    return (f'<a href="{H.escape(href, quote=True)}" style="display:inline-block;background:{bg};color:{fg};font:700 15px/1 {FONT};'
            f'text-decoration:none;padding:14px 22px;border-radius:12px;margin:4px 6px 4px 0">{H.escape(label)}</a>')


def render(trip, user) -> tuple[str, str, str]:
    """(subject, plain text, HTML) for this trip's current plan."""
    view = _view(trip)
    t, it = view["trip"], view["itinerary"]
    cur = t["currency"]
    d0, d1 = date.fromisoformat(t["start_date"]), date.fromisoformat(t["end_date"])
    span = f"{d0:%a %d %b}" if d0 == d1 else f"{d0:%d %b}–{d1:%d %b}"
    who = f"{t['travellers']} traveller{'s' if t['travellers'] != 1 else ''}"
    title = it["title"] or t["title"]
    url = trip_url(trip)
    subject = f"✈️ Your Yaitro plan is ready: {title} · {span}"
    text = _plain(share_service.share_text(view, url, limit=EMAIL_TEXT_LIMIT))

    days_html = []
    for n, d in enumerate(it["days"]):
        items = [i for i in d["items"] if i["kind"] != "free"]
        day_cost = sum((i.get("cost") or 0) + (i.get("travel_cost") or 0) for i in items)
        stops = sum(1 for i in items if i["kind"] in ("activity", "meal", "sunset"))
        days_html.append(
            f'<tr><td style="padding:22px 0 8px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
            f'<td style="background:{NIGHT};color:#fff;border-radius:12px;padding:12px 16px;font:700 15px/1.3 {FONT}">'
            f'📅 {H.escape(share_service._day_label(d))}'
            f'<span style="float:right;font-weight:500;color:#c9ced8;font-size:13px">{stops} stops · {fmt_money(day_cost, cur)}</span></td></tr></table></td></tr>'
            f'<tr><td><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {LINE};border-radius:12px;background:{PAPER}">'
            + "".join(_row(i, cur) for i in items) + '</table></td></tr>')
    tips = [x for x in (it.get("tips") or [])[:4] if x]
    tips_html = (f'<tr><td style="padding:22px 0 0;font:700 15px/1.4 {FONT};color:{INK}">💡 Good to know'
                 + "".join(f'<div style="font:400 14px/1.5 {FONT};color:{MUTE};padding-top:6px">• {H.escape(x)}</div>' for x in tips) + '</td></tr>') if tips else ""
    budget = view.get("budget")
    stat = lambda label, value, hi=False: (  # noqa: E731
        f'<td width="33%" valign="top" style="padding:0 5px"><div style="background:{NIGHT if hi else "#f6f7fb"};color:{"#fff" if hi else INK};border-radius:14px;padding:14px 12px">'
        f'<div style="font:500 12px/1.2 {FONT};color:{"#c9ced8" if hi else MUTE}">{label}</div>'
        f'<div style="font:800 20px/1.3 {FONT};margin-top:4px;{"color:" + LIME if hi else ""}">{value}</div></div></td>')
    stats = "".join([stat("Planned cost", fmt_money(it["total_cost"], cur), True),
                     stat("Per person", fmt_money(it["cost_per_person"], cur)) if t["travellers"] > 1 else "",
                     stat("Budget left", fmt_money(budget["remaining"], cur)) if budget else ""])
    wa_text = share_service.card_text(view, url)  # WhatsApp shows the link as the trip's card
    buttons = _button(url, "Open your plan", LIME, NIGHT)
    if user.phone:
        buttons += _button(share_service.wa_link(wa_text, user.phone), "💬 Send to my WhatsApp", "#25D366", "#06240f")
    attribution = f'<div style="padding-top:6px">{H.escape(view["google"]["attribution"])}</div>' if view["google"]["attribution"] else ""
    summary = f'<p style="font:400 15px/1.6 {FONT};color:{INK};margin:0 0 18px">{H.escape(it["summary"])}</p>' if it.get("summary") else ""
    html = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{H.escape(subject)}</title></head>
<body style="margin:0;padding:0;background:#eef0f6">
<div style="display:none;max-height:0;overflow:hidden;opacity:0">{H.escape(title)} · {H.escape(span)} · {fmt_money(it["total_cost"], cur)}. Every stop timed, every rupee counted.</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#eef0f6"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:620px">
<tr><td style="background:{NIGHT};border-radius:22px 22px 0 0;padding:30px 28px 26px">
<div style="font:700 13px/1 {FONT};letter-spacing:.14em;text-transform:uppercase;color:{LIME}">Yaitro · your plan is ready ✨</div>
<div style="font:800 28px/1.15 {FONT};color:#fff;padding-top:14px">🧳 {H.escape(title)}</div>
<div style="font:500 14px/1.5 {FONT};color:#c9ced8;padding-top:10px">📅 {H.escape(span)} &nbsp;·&nbsp; 👥 {H.escape(who)} &nbsp;·&nbsp; 📍 {H.escape(t["destination"])}</div>
</td></tr>
<tr><td style="background:{PAPER};padding:24px 22px 8px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>{stats}</tr></table>
<div style="height:20px"></div>{summary}
<div>{buttons}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{"".join(days_html)}{tips_html}</table>
</td></tr>
<tr><td style="background:{PAPER};padding:18px 22px 30px;border-radius:0 0 22px 22px">
<div style="border-top:1px solid {LINE};padding-top:16px;font:400 12px/1.6 {FONT};color:{MUTE}">
Costs marked (est.) are estimates; train times and fares come from real timetables where we have them and say so where we don't.
<div style="padding-top:6px">You get this because you made a plan on Yaitro. <a href="{H.escape(get_settings().public_app_url.rstrip("/"), quote=True)}/account" style="color:{MUTE}">Turn plan emails off</a>.</div>
{attribution}</div></td></tr>
</table></td></tr></table></body></html>"""
    return subject, text, html


def send_for(db, user: User, trip) -> str:
    """Email this trip's current plan to the account's own address. Returns the masked address. Raises NotReady / mailer.MailError."""
    if trip.current_itinerary is None:
        raise NotReady("This trip has no plan yet.")
    if not user.is_active or not user.email_verified:
        raise NotReady("This account's email isn't verified.")
    subject, text, html = render(trip, user)
    mailer.send_mail(user.email, subject, text, html)
    from app.services.verification_codes import mask
    return mask("email", user.email)


def _work(user_id, trip_id) -> None:
    try:
        with SessionLocal() as db:
            user = db.get(User, user_id)
            if user is None or not user.email_plans:
                return
            trip = TS.load_trip(db, trip_id, owner=user)
            to = send_for(db, user, trip)
        log.info("plan emailed to %s (trip %s)", to, trip_id)
    except Exception as exc:  # noqa: BLE001 - a plan email must never be a reason for anything else to fail (I3)
        log.warning("plan email for trip %s was not sent: %s", trip_id, exc)


def after_plan(user_id, trip_id) -> None:
    """A NEW plan has just been saved. Email it to its owner in the background (inline in tests, so they are deterministic)."""
    if killswitch.killed("plan_emails") or not mailer.configured():
        return
    if get_settings().environment == "test":
        _work(user_id, trip_id)
    else:
        threading.Thread(target=_work, args=(user_id, trip_id), daemon=True, name="plan-email").start()
