# Runbook

How the newsroom runs day to day, and what to do when something goes wrong.

## The daily rhythm (target: under 30 minutes a day during the pilot)

| When (Irish time) | What happens | Who |
|---|---|---|
| 06:17, 09:17, 12:17, 15:17, 18:17 | Pipeline finds, verifies and drafts up to 6 stories per run; each becomes a pull request labelled `story` | automatic |
| Twice a day, e.g. 08:00 and 16:00 | Review open `story` pull requests on the GitHub app | editor |
| On merge | The editor's name is stamped on the story, the site rebuilds and deploys | automatic |
| 06:40 weekdays | The Better Brief is built from stories live for at least 6 hours and sent to the email provider as a draft | automatic; editor clicks send (or switch to scheduled) |
| 07:10, 13:10, 18:10 | Carousel slides, captions and vertical videos for the newest stories are rendered | automatic; post or schedule them |
| Every 6 hours | Sources behind recent stories are re-checked; changes open an issue labelled `corrections` | automatic; editor acts |
| Mondays | Progress trackers refresh from Our World in Data | automatic |

## Reviewing a story (the pull request)

The pull request description shows: why it needs a human, every check result, the AI
fact-check verdict and fixes, and every claim with the exact passage and link that supports it.

- **Approve:** merge. Edit the story file in the pull request first if a word needs changing.
  Any figure or quote you change must still match a passage in the evidence.
- **Reject:** close with a one-line reason (e.g. "not progress", "single source", "tone").
- **Unsure:** leave it. Drafts older than 48 hours should be closed.

Checks marked FAIL mean the story must not be merged as it stands. WARN on `entities`
means a name wasn't found word for word in the evidence: check spelling against the source.

## Corrections

```bash
python -m gnn correct <slug> --kind correction --note "We said 48%; the operator's figure is 47%." --editor "Your Name"
```

Then edit the body or front matter in the same commit if the text needs fixing. Kinds:
`correction` (we got a fact wrong), `clarification` (accurate but misleading), `update`
(new information), `retraction` (the story should not stand; the page stays with a notice).
The previous version is archived in `data/versions/`, the note appears on the story and on
`/corrections/`, and the audit log records who did it. Correct the newsletter in the next issue.

Targets: acknowledge within 24 hours, decide within 72 hours, unpublish within 1 hour if a
harmful error is confirmed.

## Stopping everything (kill switch)

- Pause the pipeline: set repository variable `PIPELINE_ENABLED` to `false`.
- Pause the newsletter: set `NEWSLETTER_ENABLED` to `false`.
- Take a story down: `python -m gnn correct <slug> --kind retraction --note "..." --editor "..."` and push.
- Stop all AI spend: revoke the key in the Anthropic console, or set `llm.daily_spend_cap_eur: 0`.

## Costs and limits

`config/pipeline.yaml` caps AI spend per day (`daily_spend_cap_eur`) and per story
(`per_story_cap_eur`). Every call is logged to `data/cost-log.jsonl` (not committed) with tokens,
cost, model and prompt version. Also set a monthly spend limit in the Anthropic console.

## Pilot targets before switching on auto-publish

Stay in `editorial.mode: review_all` until the first 100 stories show:
- correction rate under 2%, and no correction of a material fact in the last 50 stories
- at least 80% of drafts approved without edits to facts
- median editor time under 4 minutes per story
- zero copying-check failures reaching review in the last 50 stories

Even then, auto-publish shows an "AI-generated, not reviewed" label (AI Act Art 50(4)),
makes the publisher ineligible for EMFA Article 18 platform privileges, and needs the about,
how-we-work and AI policy pages updated first. Most publishers will want to stay in review mode.

## Adding a source

1. Add it to `config/sources.yaml` with `rights`, `licence`, `tier`, `owner_org`.
2. Read its licence and terms; record anything unusual in `note`.
3. `python -m gnn sources` to test the feed.
4. The first 20 stories from any new source always go to review.

## Model and prompt changes

Prompts are in `prompts/`. Each evidence file records the hash of the prompts used. Before
changing a prompt or a model in `config/pipeline.yaml`, run the pipeline on a fixed set of
past candidates and compare approval and check-failure rates. Model IDs are pinned in config;
check https://platform.claude.com/docs/en/about-claude/model-deprecations when a model is
announced for retirement.

## Due diligence pack (for a future buyer)

Everything a buyer asks for is already in the repository: audit log (`python -m gnn audit`
verifies the hash chain), evidence files, version history, source and licence register,
prompts with version hashes, policies, and git history of every decision.
