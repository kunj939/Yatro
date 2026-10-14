# ADR 0017: Ask about any stop, with Wikipedia for what a place is

Date: 2026-10-15. Status: accepted.

**Context.** Travellers want to ask about each stop as they read the plan: why a place is famous, a little of its
history, how long to spend there. The trip chat (ADR 0016) answered only from the plan, and a plan says when and how
much, not what a place is.

**Decision.**

- **Ask on every stop.** Each sight, meal and stay in the timeline has an "Ask" button: an icon on a compact row, and
  "Ask: why famous?" on a detailed card. It sends the question to the trip chat that is on screen, with the stop's
  `item_id`. The chat keeps that stop ("Asking about Hawa Mahal") for follow-ups until the traveller clears it or the
  plan changes. Follow-up chips depend on the kind of stop:
  - sights: "Why is it famous?", "How long should I spend?", "Is it open then?", "Entry fee?";
  - meals and stays: "Is it open then?", "How much is it?", "How long is it?".
- **Asking by name.** A question that names a stop in the plan ("why is Jantar Mantar famous?") finds that stop
  without a tap. Plan text pasted into the chat finds its stop the same way.
- **"Skip it"** said about a tapped stop becomes "skip <that stop>" for the re-planner.
- **Where "what it is" comes from.** Text comes from `providers/wikipedia.py`, which uses the article's own
  introduction (up to 6 sentences, pronunciation brackets removed). The article is chosen as follows:
  - a place from Wikidata uses its exact English article;
  - any other place uses a search for its name and the destination, kept only when the title names the place: all
    distinctive words of a one- or two-word name, or three in five for longer names;
  - otherwise the name itself through redirects ("Amer Fort" → "Amber Fort"), kept only when the article mentions the
    destination, so New York's Central Park is never Jaipur's.

  Articles are looked up only for sights, not restaurants or hotels: the wrong article would mislead. Results are
  cached 30 days (`wikipedia` TTL) and go through the gateway (provider `wikipedia`, 5 s).
- **Licence.** Wikipedia text is CC BY-SA. An answer that quotes it is marked "(Wikipedia)" and links to the article
  ("Read more on Wikipedia"). An answer that doesn't quote it carries no link.
- **The answer.**
  - With a model: the model gets the trip, the stop and the article, and is told to explain the place only from the
    article and fit it to the trip from the plan.
  - Without a model: rules answer how long (the planned time, and the usual visit length when it differs), opening
    hours, and entry fee from the plan. "Why famous" and anything else get the article's introduction plus "In your
    plan: Day N, time, hours, cost".
  - With no article: the plan's own reason for the stop, and a plain sentence saying there's no article to tell more.
- **Switch.** `WIKIPEDIA_ENABLED` (default on). Tests turn it off, so the suite stays offline; the tests that cover
  it stub the lookup.

**UI.** The chat card is roomier:
- a header with the assistant's icon;
- a taller conversation, with an avatar on answers;
- bigger text and a "thinking" indicator;
- the newest question scrolled to the top, so a long answer is read from its first line.

**Consequences.**
- Wikipedia is called at most once per place a month for all travellers: free, and with no key.
- The chat never states facts about a place that aren't in its article or the plan.
