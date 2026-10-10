import type {
  InitiativeDraft,
  InitiativeSuggestion,
  InitiativeSuggestionSet,
  SuggestedItem,
} from "../../../api/client";

/** One theme as the operator has it: from the draft, or made here from an ungrouped milestone. */
export interface ThemeEdit {
  key: string;
  title: string;
  description: string;
  rationale: string;
  keep: boolean;
}

export interface SprintEdit {
  keep: boolean;
  title: string;
  description: string;
  /** The title the draft gave it, so a redraw can tell an edit from a default. */
  draftTitle: string;
  draftDescription: string;
  /** Item ids unticked; everything else is attached. */
  dropped: Set<string>;
}

/**
 * Everything the operator changed on one set of suggestions. Milestones are
 * assigned rather than ticked, so one can move between themes and in or out of
 * the ungrouped list without being lost.
 */
export interface ReviewState {
  themes: ThemeEdit[];
  /** milestone id -> theme key, or null for "not grouped". */
  assign: Record<string, string | null>;
  sprint: SprintEdit | null;
}

/** Keyword themes are a rough draft, so none is ticked until someone reads it; the sprint is. */
function freshTheme(s: InitiativeSuggestion, keep: boolean): ThemeEdit {
  return { key: s.key, title: s.title, description: s.description, rationale: s.rationale, keep };
}

function sprintOf(data: InitiativeSuggestionSet): InitiativeSuggestion | undefined {
  return data.suggestions.find((s) => s.kind === "sprint");
}

export function themesOf(data: InitiativeSuggestionSet): InitiativeSuggestion[] {
  return data.suggestions.filter((s) => s.kind === "theme");
}

/** Every milestone the review can place: the themes' and the ungrouped ones. */
export function milestonesOf(data: InitiativeSuggestionSet): SuggestedItem[] {
  return [...themesOf(data).flatMap((s) => s.items), ...data.ungrouped];
}

/**
 * The review for `data`, carrying over whatever `prev` changed that still
 * applies. A redraw (another sprint length, a refetch after a conflict) keeps
 * titles, ticks, moves and unticked items; a milestone that is gone is simply
 * not there to place.
 */
export function reconcile(prev: ReviewState | undefined, data: InitiativeSuggestionSet): ReviewState {
  const keepByDefault = data.source === "agent";
  const prevThemes = new Map((prev?.themes ?? []).map((t) => [t.key, t]));
  const themes = themesOf(data).map((s) => {
    const before = prevThemes.get(s.key);
    return before ? { ...before, rationale: s.rationale } : freshTheme(s, keepByDefault);
  });
  const drafted = new Set(themes.map((t) => t.key));
  // Themes made here survive a redraw; their milestones come with them below.
  for (const theme of prev?.themes ?? []) {
    if (theme.key.startsWith("custom:") && !drafted.has(theme.key)) themes.push(theme);
  }
  const keys = new Set(themes.map((t) => t.key));

  const assign: Record<string, string | null> = {};
  for (const s of themesOf(data)) for (const m of s.items) assign[m.id] = s.key;
  for (const m of data.ungrouped) assign[m.id] = null;
  for (const id of Object.keys(assign)) {
    const before = prev?.assign[id];
    if (before === null || (before !== undefined && keys.has(before))) assign[id] = before;
  }

  const sprintSuggestion = sprintOf(data);
  let sprint: SprintEdit | null = null;
  if (sprintSuggestion) {
    const before = prev?.sprint;
    const ids = new Set(sprintSuggestion.items.map((i) => i.id));
    sprint = {
      keep: before?.keep ?? true,
      title: before && before.title !== before.draftTitle ? before.title : sprintSuggestion.title,
      description:
        before && before.description !== before.draftDescription
          ? before.description
          : sprintSuggestion.description,
      draftTitle: sprintSuggestion.title,
      draftDescription: sprintSuggestion.description,
      dropped: new Set([...(before?.dropped ?? [])].filter((id) => ids.has(id))),
    };
  }
  return { themes, assign, sprint };
}

export function membersOf(review: ReviewState, key: string, milestones: SuggestedItem[]): SuggestedItem[] {
  return milestones.filter((m) => review.assign[m.id] === key);
}

/** A new theme holding one milestone, named after it until the operator renames it. */
export function withCustomTheme(review: ReviewState, milestone: SuggestedItem): ReviewState {
  const key = `custom:${milestone.id}`;
  return {
    ...review,
    themes: [
      ...review.themes.filter((t) => t.key !== key),
      { key, title: milestone.title, description: "", rationale: "Started here by hand.", keep: true },
    ],
    assign: { ...review.assign, [milestone.id]: key },
  };
}

export interface PlannedInitiative extends InitiativeDraft {
  kind: "theme" | "sprint";
  /** Features and bugs that leave their milestones. */
  leaving: number;
  /** Milestones the sprint takes the last open work out of, as things stand. */
  empties: string[];
}

/** Sprint items still ticked. */
export function keptSprintItems(sprint: InitiativeSuggestion, edit: SprintEdit): SuggestedItem[] {
  return sprint.items.filter((i) => !edit.dropped.has(i.id));
}

/**
 * Milestones a sprint still empties. The server names the ones the whole draft
 * would; unticking any of a milestone's items leaves it open work, so it drops out.
 */
export function stillEmptied(sprint: InitiativeSuggestion, edit: SprintEdit): string[] {
  return sprint.empties.filter((m) =>
    sprint.items.filter((i) => i.from_milestone === m).every((i) => !edit.dropped.has(i.id)),
  );
}

/** What pressing Create would write, in the order it is written. */
export function plannedInitiatives(data: InitiativeSuggestionSet, review: ReviewState): PlannedInitiative[] {
  const milestones = milestonesOf(data);
  const planned: PlannedInitiative[] = [];
  for (const theme of review.themes) {
    const members = membersOf(review, theme.key, milestones);
    if (!theme.keep || members.length === 0) continue;
    planned.push({
      kind: "theme",
      title: theme.title.trim(),
      description: theme.description.trim(),
      item_ids: members.map((m) => m.id),
      target_date: null,
      leaving: 0,
      empties: [],
    });
  }
  const sprint = sprintOf(data);
  if (sprint && review.sprint?.keep) {
    const items = keptSprintItems(sprint, review.sprint);
    if (items.length > 0) {
      planned.push({
        kind: "sprint",
        title: review.sprint.title.trim(),
        description: review.sprint.description.trim(),
        item_ids: items.map((i) => i.id),
        target_date: sprint.target_date,
        leaving: items.length,
        empties: stillEmptied(sprint, review.sprint),
      });
    }
  }
  return planned;
}
