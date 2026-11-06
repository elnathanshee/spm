# Sequence diagrams (UML, Week 5)

Mermaid source (`.mmd`) is the source of truth; the `.png` files are rendered copies for the Drive evidence.
Update the `.mmd` when the flow changes (e.g. Sprint 3: the unassigned queue for Change #5 replaces round-robin in Sequence 1).

| File | Scenario | Stories |
|---|---|---|
| 01-submit-and-first-assignment | Organiser submits; round-robin assigns a Coordinator; notifications | SCRUM-19, 22, 51, 63 |
| 02-review-decision | Assigned Coordinator returns / approves / rejects; Organiser notified | SCRUM-25, 26, 27, 51 |
| 03-lead-reassigns | Event Coordinator Lead reassigns; history recorded; three people notified | SCRUM-23 (Week 7 Change #5) |
| 04-request-to-venue-booking | Coordinator searches suitability, requests booking with hold; Venue Staff approves | SCRUM-33, 35, 38, 81, 89, 91 |

Render locally: `npx @mermaid-js/mermaid-cli -i 01-submit-and-first-assignment.mmd -o 01-submit-and-first-assignment.png`
or paste the `.mmd` into https://mermaid.live. GitHub also renders Mermaid inside ```mermaid blocks in Markdown.

Drawn from the code on main as of 5 Oct 2026 (after PR #10).
