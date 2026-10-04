# Good News News

An evidence-first, AI-assisted newsroom for verified progress news: what improved, the
evidence behind it, and what we still don't know. Based in Ireland, written for everyone.
("Good News News" is a working title. The brand lives in `config/site.yaml`.)

The whole operation runs from this repository on free infrastructure:

```
 sources.yaml ─► discover ─► triage ─► fetch evidence ─► extract claims ─► verify spans
 (licensed &      (RSS,      (AI)      (robots.txt +      (AI, exact      (code: every
  primary feeds)   GDELT)               TDM opt-outs)      passages)       passage must
                                                                           match the page)
        ─► draft (AI, from verified claims only) ─► deterministic checks ─► AI fact-check
           numbers · dates · quotes · names · hype words · copying · structure   (fail closed)
        ─► editorial gate ─► pull request per story ─► editor merges = approval
        ─► site (static) · The Better Brief (email) · carousel + vertical video · source monitor
```

Every published story shows its sources, a "Why this matters" section, its limitations,
and a downloadable evidence file listing each claim with the exact passage that supports it.
Corrections are dated, versioned and listed publicly. An append-only, hash-chained audit log
(`data/audit.jsonl`) records every decision.

## Quick start

```bash
pip install -r requirements.txt
python -m pytest -q                 # 20+ tests, no network needed
python -m gnn demo                  # offline end-to-end run on fictional samples → prints a dist/ path
```

With an Anthropic API key (`ANTHROPIC_API_KEY`) and network access:

```bash
python -m gnn sources               # test every feed in config/sources.yaml
python -m gnn run                   # one pipeline run; drafts land in queue/
python -m gnn queue                 # list drafts
python -m gnn approve <id> --editor "Your Name"
python -m gnn build                 # static site → dist/
python -m gnn newsletter            # today's Better Brief → content/newsletter/
python -m gnn social --video        # carousel slides + vertical video → dist/social/
```

## Where things live

| Path | What |
|---|---|
| `config/site.yaml` | brand, publisher details, sections, newsletter provider |
| `config/pipeline.yaml` | editorial mode, thresholds, models, spend caps |
| `config/sources.yaml` | source register with licence and rights tier for each feed |
| `prompts/` | the four editorial prompts (versioned by hash in each evidence file) |
| `gnn/` | the pipeline, checks, site generator, newsletter, social renderer |
| `content/stories/` | published stories (Markdown + YAML, see `docs/STORY-FORMAT.md`) |
| `content/pages/` | about, standards and legal pages (drafts for solicitor review) |
| `data/evidence/` | one evidence file per story (published alongside it) |
| `.github/workflows/` | scheduled pipeline, publish-on-merge, newsletter, social, monitor, trackers |
| `docs/` | `RUNBOOK.md` (daily operation), `MORNING-CHECKLIST.md` (go-live steps) |

## Design choices

- **Rights first.** Third-party journalism is a lead, never a source to rewrite: its text is
  never given to the drafting model, and its facts must be confirmed in a primary document.
  Text-and-data-mining reservations (robots.txt, `tdm-reservation`, TDMRep, `noai`) are honoured.
- **Fail closed.** A failed check, a model refusal or a spend cap stops a story, not the checks.
- **Human editorial control by default** (`editorial.mode: review_all`), which is what the
  EU AI Act Article 50(4) exemption and EMFA Article 18 require.
- **No cookies, no third-party requests.** Fonts are self-hosted; analytics is optional and cookie-free.
- **Git is the database.** Stories, evidence, versions and the audit log are plain files with
  full history, which makes the business easy to audit and to hand over.
