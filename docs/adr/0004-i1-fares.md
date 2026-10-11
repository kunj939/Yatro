# ADR 0004 — I1 amended: fares

Status: **Accepted and implemented** (user decision 2026-10-06). Date: 2026-10-06. Amends ADR 0002, "Interpretation of I1
'fare'".

## Decision (the user's wording)

> **I1.** A train name, number, departure time or arrival time reaches a user ONLY from real timetable data (≤45 days, 30
> in the seasonal cap). A fare may be shown either as a real fare from a provider (labelled verified) or as an estimate
> from our deterministic fare model (labelled "Estimated fare"). AI may never produce or modify any fare, name, number
> or time.

Further rules:
- An estimated fare is never labelled or styled like a verified one.
- For `station_guidance` results (no specific train), the planner may use an estimated fare for the distance and class,
  so the budget still works. It is shown as "Approx. train fare for this distance", with no train name attached.

## Implementation

| Where | What |
|---|---|
| `train_fact_gate.fare_item` (new; last step of `trip_service.item_out`) | Sets the label from the fare's own state, as below. It removes any `fare_label` or `fare_note` written upstream, so only this gate decides. |
| `llm_service.MONEY` and `violations_in` | Any amount of money in AI text is a violation, **even one copied exactly from the data**. That field falls back to template text. Covers ₹, $, €, £, ¥, ₩, ₫ and ฿; codes such as Rs, INR, USD and EUR; "500 rupees", "45 dollars", "650/-" and "₹5k". The narration prompt now says not to write amounts. |
| `rail_sources.rail_text_ok` | Already rejected amounts in AI rail guidance (ADR 0002). Unchanged. |
| `VerificationBadge` (frontend) | New `label` prop. It changes the text only; colour and icon still come from the state. |
| `TimelineItem` (frontend) | Shows the fare label in the badge, and a downgrade note when present. |

How `fare_item` labels a transport fare:

| The fare's state | Label |
|---|---|
| `verified_*` | "Verified fare". Today only Google transit fares are verified. |
| `demo` | "Sample fare" |
| a train shown with no time (station guidance, or redacted by the I1 gate) | "Approx. train fare for this distance" |
| anything else | "Estimated fare" |

**A verified fare is never downgraded silently.** A downgrade happens in one case only: a verified fare whose train can no
longer be shown, because its timetable is past the I1 limit. Then:
- the state becomes `estimated`;
- `meta.fare_note` says so: "This fare was confirmed for a specific train that can no longer be shown, so it is now an
  estimate";
- the note is shown on the ticket.

**Where fares come from today:**
- RailRadar and IRCTC1 train fares, one-change connections and the station-network estimate all come from the
  deterministic `fare_model` and are `estimated`.
- Google transit fares are `verified_provider` when Google returns a fare, otherwise `estimated`.

## Tests

`tests/test_fares.py` (9 tests):
- an estimated fare always carries the Estimated label;
- nothing upstream can label an estimate as verified;
- station guidance shows the approximate-fare label, with no name, number or time;
- a verified fare stays verified;
- a verified fare downgraded by redaction carries the note;
- sample data, non-transport items and zero-cost items are left alone;
- AI narration containing amounts is rejected field by field, even when copied from the data;
- every money form is caught;
- end to end, every transport fare in a planned trip is labelled, and "Verified fare" appears exactly when the state is
  verified.

**Changed test.** `test_engine.test_fact_guard_rejects_invented_numbers` used to allow the AI to copy "₹4,720" from the
data. That is now a violation, as the amended I1 requires.

## Consequences

- AI summaries can no longer mention money. Costs appear only in the deterministic UI (badges, budget panel) and in
  template text.
- More narrations may come back `partial` until the model follows the new prompt line.
- Fare state now also governs styling. A future provider fare must set `verified_provider` explicitly to be shown as
  verified.
