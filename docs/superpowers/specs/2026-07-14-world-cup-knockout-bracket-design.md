# World Cup Knockout Bracket Design

## Goal

Add a live, two-sided World Cup knockout bracket below the group tables on the Results tab. The bracket covers the Round of 32 through the final, includes the third-place match, and uses the existing World Cup fixture and team data without inventing participants or results.

## Scope

- Render only when the active league is World Cup.
- Place the bracket after all Group A-L tables in the Results tab.
- Show Round of 32, Round of 16, quarterfinals, semifinals, final, and third place.
- Re-render from the current `D.fx` and `D.tm` state after every normal page render or live refresh.
- Preserve all existing PL, La Liga, group-table, guessing, comparison, and AI behavior.

## Layout

The bracket is a fixed-format, mirrored canvas with nine stage columns:

1. Left Round of 32: 8 matches
2. Left Round of 16: 4 matches
3. Left quarterfinals: 2 matches
4. Left semifinal: 1 match
5. Center: final and third-place match
6. Right semifinal: 1 match
7. Right quarterfinals: 2 matches
8. Right Round of 16: 4 matches
9. Right Round of 32: 8 matches

The inner canvas has a stable minimum width of approximately 1,180-1,260 pixels. Desktop viewports show the full bracket when space allows. Narrow viewports use horizontal scrolling instead of shrinking labels below a readable size. On the first mobile render, the scroll position centers the final. Later live refreshes must not pull the user away from their current scroll position.

The final is the visual focal point. The third-place match sits below it as a separate center card. It may use dashed semifinal-loser connectors so it is distinguishable from the championship path.

## Match Cards

Every card shows:

- Stage-aware match position.
- Both team logos and short names.
- Score when started, or Israel-local kickoff time when scheduled.
- `FT` for completed matches.
- Existing live label and minute for in-progress matches.
- A neutral placeholder treatment while a participant is unresolved.

A completed winner receives the World Cup success color and a stronger team-row treatment. Live cards use the existing World Cup live color. Pending cards remain visually quiet. Placeholder teams keep their supplied slot label; the UI must not replace them with predicted teams.

## Data Model And Round Detection

The bracket consumes the existing `D.fx` and `D.tm` objects. It does not add a second network request or persistent state.

Construction steps:

1. Select fixtures without `grp`; these are knockout fixtures.
2. Sort by kickoff and fixture id for deterministic ordering.
3. Require the expected 32 knockout fixtures before drawing a complete tree.
4. Split them by official tournament counts:
   - 16 Round of 32
   - 8 Round of 16
   - 4 quarterfinals
   - 2 semifinals
   - 1 third-place match
   - 1 final
5. Build source-to-destination links for every participant slot.

Link resolution uses the safest available evidence in this order:

1. A real participant id found in a previous-round fixture.
2. A placeholder name that identifies the source round and match number, such as `Semifinal 1 Winner`.
3. A fixed 2026 tournament slot map validated against the published 32-fixture schedule.

The current 2026 fallback slot map is:

- Round of 32 to Round of 16: `[0,3] [2,5] [1,4] [6,7] [11,10] [9,8] [14,13] [12,15]`
- Round of 16 to quarterfinals: `[1,0] [4,5] [2,3] [6,7]`
- Quarterfinals to semifinals: `[0,1] [2,3]`
- Both semifinals feed the final; their losers feed third place.

If the fixture count or link graph is inconsistent, the page shows a compact unavailable state instead of drawing a misleading bracket.

## Connector Rendering

Match cards are normal HTML elements in a stable CSS grid. A decorative SVG layer behind the cards draws orthogonal connectors from measured card edges. The SVG is `aria-hidden` and has no pointer events.

Connector state is derived from the destination participant:

- Pending: muted line.
- Confirmed qualifier: highlighted line matching the winning card.
- Third-place path: dashed line.

Connector coordinates are recalculated after bracket render and on viewport resize. The calculation is local only; it must not trigger a full application render.

## Live Updates

`rRes()` appends the bracket after `rWCGroups()`. Each existing live-data update already replaces `D.fx` and calls `render()`, so the bracket naturally receives new teams, scores, statuses, and minutes.

Live behavior must satisfy these rules:

- A newly resolved participant replaces its placeholder on the next render.
- A score update changes the card without changing bracket slot identity.
- A completed winner highlights the correct outgoing path.
- Penalty or extra-time ties must not infer a winner from the displayed score alone. Qualification is confirmed by the team appearing in the next-round fixture or by an explicit placeholder/source mapping.
- A live refresh preserves the user's horizontal scroll position when the bracket already exists.

## Accessibility And Responsive Behavior

- The scroll container is keyboard focusable and has an accessible bracket label.
- Match cards expose readable team, stage, status, score, and kickoff text without relying on connector color.
- Team images include useful alt text and retain the existing image-error fallback.
- Card dimensions are stable so live labels and score changes do not shift the tree.
- No page-level horizontal overflow is allowed; only the bracket's own scroll container may scroll horizontally.
- The bottom navigation must not cover the final visible bracket rows on mobile.

## Testing

Automated tests cover:

- Deterministic 16/8/4/2/1/1 round partitioning.
- The 2026 fallback slot map.
- Real-team and placeholder source-link resolution.
- Tied knockout scores that are resolved by later-round participation.
- Safe unavailable state for incomplete or inconsistent data.
- Bracket inclusion only on the World Cup Results tab.
- Live, finished, pending, and placeholder card rendering.
- No changes to PL or La Liga rendering.

Browser verification covers 1440x900 and 390x844 viewports:

- All stages and 32 knockout matches render.
- The center final is the initial mobile focus.
- Horizontal bracket scrolling works without page overflow.
- Team names, logos, scores, live minute, and connectors do not overlap.
- Live re-render preserves the bracket scroll position.

## Out Of Scope

- Editing the official bracket.
- Predicting unresolved participants.
- Adding a 2022 World Cup archive.
- Replacing the existing day-based Results navigation.
- Adding a new data provider or backend service.
