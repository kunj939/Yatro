// Mirrors backend/app/services/interest_tags.py (INTEREST_CHIPS keys + INTEREST_LABELS). Keep the ids and
// order in step with that file if either changes — the backend is the source of truth for what a chip id
// expands to and for validation; this file only needs the id and a label to render.
export interface InterestChip { id: string; label: string }

export const INTEREST_CHIPS: InterestChip[] = [
  { id: "forts_palaces", label: "Forts & palaces" },
  { id: "temples_spiritual", label: "Temples & spiritual sites" },
  { id: "nature_lakes", label: "Nature & lakes" },
  { id: "street_food", label: "Street food" },
  { id: "museums_art", label: "Museums & art" },
  { id: "markets_shopping", label: "Markets & shopping" },
  { id: "adventure", label: "Adventure" },
  { id: "nightlife", label: "Nightlife" },
  { id: "heritage_history", label: "Heritage & history" },
  { id: "viewpoints_sunsets", label: "Viewpoints & sunsets" },
];

export const MAX_INTERESTS = 6;

export function interestLabel(id: string): string {
  return INTEREST_CHIPS.find((c) => c.id === id)?.label ?? id;
}