# Phase 3: interview with decision cards

> Detail for the phases of the `kensa-review` skill. `SKILL.md` has the safety rules and the overall flow; read this file when you start the phases below. `W` is the command defined in `SKILL.md` (`uv run …/scripts/kensa.py`).

## Phase 3: Interview with decision cards (organization, process, business decisions)

`W cards next -n 3` prints the **cards** for the items that evidence did not settle. One card handles several best practices together (30 cards cover the 155 items that may need an interview). **For items that have detected evidence (a draft), it appears at the top of the card as "detected evidence".** Use it to confirm ("it looks like ○, is that right?") rather than asking from zero.

1. Show the card to the user as it is (or tidied up a little). Format: **question → four options for the current state (○ Done / △ Partial / ✗ Not done / N/A) → ★ recommendation and steps → alternatives (when to choose each, and the trade-offs)**. The recommendations suit a small team of about four people, and `{po}`, `{chat}`, `{vcs}` and so on are filled from the `context` values.
   - Cards that start from technical terms (RTO/RPO, SLO, forensics, ORR, game days) carry a **question in business language (`ask_plain`)**. When you also ask PO or sales people, ask with that one first, and add the technical terms only when needed.
   - If a multiple-choice question tool is available (such as Claude Code's AskUserQuestion), ask the current-state choice (full/partial/none) as multiple choice, one to three cards at a time. Always make the recommendation the first option, labelled (Recommended).
   - If the user says "just decide everything", decide the **current state only from facts you actually asked about**. Proceed with items you cannot know as ✗ (adopting the recommendation), and add `--note "provisional answer"`. Do not invent facts.
2. Record answers with `W cards answer C-XXX --current full|partial|none|na --choose recommend|alt1|alt2|current [--note "…"] [--evidence URL] [--override CHOICE_ID=○]`.
   - When items inside a card differ in status, override them individually with `--override`.
   - `full` (○) is self-reported. If there is supporting evidence (a URL, a document, a setting), attach it with `--evidence` so it can be checked later. If there is none, it appears in OpenItems.
   - Items that have strong AWS/repository evidence (a route that is not `draft`) are not overwritten by a self-report (only with `--force`). Draft (`draft`) evidence is overwritten by the person's answer (the person knows the reality). Conversely, draft evidence that comes from a rescan after the person answered does not overwrite that answer (only with `--force`).
   - The recommendations are defaults for a small team. If the team size, tools or criticality do not fit (a dedicated SRE exists, the chat tool is different, and so on), override them for that organization in `.kensa/cards.local.json` (`{"cards": {"C-ID": {"recommend": {...}}}}`; the bundled cards are not changed).
   - Adopting the recommendation fills the "Org shape / tools" column with the operating form made concrete with that team's tools, and also fills Recommended action, Effort, Priority and Owner.
3. For items that no card covers (those left in `items --unresolved`), ask briefly one at a time and record them with `apply` (`channel: interview`).
