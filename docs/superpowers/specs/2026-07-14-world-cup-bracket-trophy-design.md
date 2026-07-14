# World Cup Bracket Trophy Design

## Goal

Add a realistic gold trophy image to the visual center of the World Cup knockout bracket without changing bracket data, match placement, or behavior.

## Visual Design

- Use a project-local transparent PNG so the trophy is fast and does not depend on an external image host.
- Show a realistic, front-facing gold football championship trophy with no text, branding, or surrounding frame.
- Place it in the center bracket column between the final and third-place match cards.
- Use an approximately 52px rendered width on desktop and 46px on mobile.
- Add only a restrained gold drop shadow; the trophy must not sit inside a card.

## Layout And Behavior

- Render the trophy only inside a ready World Cup knockout bracket.
- Keep it decorative with empty alternative text, `aria-hidden="true"`, and no pointer interaction.
- Place it above the connector SVG layer but outside all match cards.
- Keep card positions, connector destinations, horizontal scrolling, and mobile initial centering unchanged.
- Ensure the image cannot overlap team names, scores, live status, or the bottom navigation.

## Asset

The final transparent image lives at `static/world-cup-trophy.png`. It is generated on a flat chroma-key background and converted to a transparent PNG before it is added to the repository.

## Testing

- Assert that one trophy image appears in a ready World Cup bracket.
- Assert that it is absent from PL, La Liga, and unavailable World Cup bracket states.
- Verify that the asset loads with non-zero natural dimensions.
- Check desktop and mobile layouts for overlap and page-level horizontal overflow.
- Confirm that live refresh, resize, and bracket navigation behavior remain unchanged.

## Out Of Scope

- Using an official FIFA-hosted image or logo.
- Changing the bracket structure, fixtures, predictions, or match results.
- Adding animation or a trophy interaction.
