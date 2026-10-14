# ADR 0016: A chat for each trip, and prices that follow the season

Date: 2026-10-15. Status: accepted.

## Trip chat

**Context.** "Change the plan" took changes only. A question such as "when is my train?" was read as a change, or
came back with "couldn't turn that into a change". People want to talk to their trip, by typing or by voice, and ask
about it as well as change it.

**Decision.**

- `POST /api/trips/{id}/chat` (owner only, 20 a minute) reads one message and returns either `{action: "change"}` or
  `{action: "answer", text}`.
- A change is never made here. The client sends the same message through `/replan`: the intent reader and engine
  that already change plans, with the same checks and the same limit on changes.
- **Deciding between change and question.** The rule reader runs first. A clear change that isn't phrased as a
  question goes to the planner straight away, with no model call. Otherwise the model, when one is configured,
  decides between change and answer and writes the answer.
- **What the model sees.** The model gets the plan as compact text built from the same gated item output as the trip
  page, so I1 and I6 hold. It is told to use only that data, never to invent trains, times or prices, and to say it
  can only help with this trip when asked anything else.
- **Without a model,** keyword rules answer the usual questions: transport times, hotel, money left, a given day,
  meals, weather.
- **Voice.** Speaking uses the browser's speech recognition (English India or Hindi). A question asked by voice is
  answered aloud (speech synthesis), and a mute button turns that off. No audio leaves the browser except as the
  recognised text.
- **History.** The conversation is kept per trip in the browser (sessionStorage), and the last 6 turns are sent with
  each message for context.

**Consequences.** One model call per question, logged in `llm_usage` as `chat`. Changes cost what they cost before.

## Seasonal demand on estimated prices

**Context.** Estimated flight, bus and hotel prices were flat averages. They were too low in Diwali week and over New
Year, and a flight three days out cost the same as one booked a month ahead.

**Decision.** `services/seasonality.py` gives a demand factor for a date, and the planner applies it to **estimated**
prices only. Live and verified prices already include it.

- **Festivals and holidays** (when the trip starts or ends in India), with the factors for flight / bus / hotel:

  | Season | Window | Flight | Bus | Hotel |
  |---|---|---|---|---|
  | Diwali | −6 to +4 days | 1.45 | 1.35 | 1.25 |
  | Dussehra and Durga Puja | −5 to +1 days | 1.25 | 1.2 | 1.15 |
  | Holi | −2 to +1 days | 1.2 | 1.25 | 1.1 |
  | Summer holidays | 15 May to 30 Jun | 1.15 | 1.1 | 1.1 |

  Lunar festival dates are listed for 2026 to 2028, from government holiday lists and Drik Panchang. The list needs a
  new year added each year.
- **Global peaks:**

  | Season | Applies to | Flight | Bus | Hotel |
  |---|---|---|---|---|
  | Christmas and New Year | every trip | 1.35 | 1.2 | 1.35 |
  | July and August | trips outside India | 1.15 | 1.05 | 1.15 |
- **Hotels:** Friday and Saturday nights cost 12% more. A stay's factor is the average over its nights.
- **Late booking (flights only):**

  | Booked | Factor |
  |---|---|
  | 3 days ahead or less | 1.6 |
  | 4 to 7 days ahead | 1.35 |
  | 8 to 14 days ahead | 1.15 |
  | 15 to 60 days ahead | 1.0 |
  | more than 60 days ahead | 0.95 |
- **Rail fares** don't change: the railways' fares are fixed.
- **Plan note.** One Plan note says which season raised the prices, and when the flight is booked late.
- **Cheapest workable cost.** The cheapest workable plan uses the same hotel factor, so a refusal quotes the same
  prices the plan would use.

**Consequences.** Plans in busy seasons cost more and say why. A budget that fitted before may now be refused with
fixes, which is the honest answer. The scenario sweep runs in Diwali week (7 Nov 2026) and still passes.
