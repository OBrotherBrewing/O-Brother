# Story format

Every published story is one Markdown file with YAML front matter at
`content/stories/<YYYY>/<slug>.md`. The body is the article in Markdown.
Drafts waiting for review live at `queue/<id>/story.md` in the same format, with
`evidence.json` and `checks.json` beside them.

```yaml
id: 2026-10-05-irish-wind-record-a1b2c3      # stable event id
slug: irish-wind-record
version: 1
status: published            # published | corrected | retracted
title: "Wind supplied a record share of Ireland's electricity in September"
dek: "One-sentence outcome: what changed, by how much, where."
section: planet              # a slug from config/site.yaml sections
category: A                  # A verified progress, B solution working, C human achievement,
                             # D breakthrough (early), E constructive context
tags: [energy, wind, ireland]
geography: [IE]              # ISO 3166 codes, or GLOBAL
published_at: "2026-10-05T07:00:00+00:00"
updated_at: "2026-10-05T07:00:00+00:00"
key_metric:                  # optional: the "delta" shown on cards
  label: "Wind share of electricity"
  before: "38%"
  after: "47%"
  period: "Sep 2025 to Sep 2026"
why_it_matters: "Two or three sentences."
limitations:
  - "One sentence per caveat: what we don't know yet, what could reverse it."
sources:
  - title: "Monthly system data, September 2026"
    publisher: "EirGrid"
    url: "https://..."
    accessed_at: "2026-10-05T05:12:00+00:00"
    tier: 1
    role: primary            # primary | corroborating | lead
    rights: LICENSED
review:
  mode: reviewed             # reviewed (named editor signed off) | auto (AI label shown)
  editor: "Editor Name"
  reviewed_at: "2026-10-05T06:40:00+00:00"
ai_disclosure: "Drafted with AI from the sources below; checked claim by claim against them and reviewed by Editor Name."
scores: {positivity: 82, significance: 64, novelty: 55, risk: 8}
evidence: evidence/2026-10-05-irish-wind-record-a1b2c3.json
social:
  hook: "Short scroll-stopping first line, no hype words."
  slides:                    # 3-5 short slides for carousel/video, each <= 220 characters
    - "..."
  caption: "Post caption with source credit."
newsletter_blurb: "One or two sentences for The Better Brief."
corrections:
  - date: "2026-10-06"
    type: correction         # correction | clarification | update | retraction
    note: "We originally said 48%; the operator's figure is 47%."
```

## Evidence bundle (`evidence/<id>.json`, published with the story)

```json
{
  "id": "...",
  "documents": [
    {"n": 1, "url": "...", "publisher": "...", "owner_org": "...", "tier": 1, "rights": "LICENSED",
     "retrieved_at": "...", "sha256": "...", "title": "..."}
  ],
  "claims": [
    {"id": "c1", "text": "Wind supplied 47% of electricity in September 2026.", "kind": "number",
     "doc": 1, "span": "wind generation accounted for 47% of ...", "status": "supported"}
  ],
  "checks": {"numbers": "pass", "dates": "pass", "quotes": "pass", "entities": "pass",
             "hype": "pass", "copying": "pass", "factcheck": "pass"},
  "corroboration": "primary_confirmed"
}
```

Full source text is never committed: snapshots stay in `.cache/snapshots/` (and private storage in
production). Only URLs, hashes and short supporting spans are published.
