# Go-live checklist

Everything that needs a person: a decision, a payment, an account or a signature.
Items are in dependency order. Where a step says "Claude can do this", reply in the
session with the go-ahead (and any key it needs) and it will be done for you.

## A. Decisions (10 minutes)

1. **Positioning.** Approve "verified progress news" (constructive, evidence-first) as the
   proposition, with "good news" used only as everyday shorthand. *Recommended.*
2. **Name.** Choose from the shortlist in the private decision pack, or ask for another round.
   Every finalist needs a proper trade mark search before money is spent on branding.
3. **Editor of record.** Name the person who reviews stories and holds editorial
   responsibility (you, at first, about 30 minutes a day). Required for the EU AI Act
   Art 50(4) exemption, EMFA Art 18 and the defamation "reasonable checks" defence.
4. **Publishing mode.** Keep `review_all` (every story approved by the editor). *Recommended.*
5. **Repository.** Move this code to a new **private** repository (drafts awaiting review must
   not be public). *Claude can do this* once you say which name to use.
6. **Legal entity.** Publish through a new Irish company (cleanest for a future sale) rather
   than an existing business. Needs a CRO incorporation.
7. **Pilot budget and stop rules.** Approve a 3-month pilot budget and the month-3 and month-6
   continue/stop thresholds in the decision pack.

## B. Accounts and payments (in this order)

| # | What | Typical cost | Who |
|---|---|---|---|
| 1 | Register the chosen .com (Cloudflare Registrar sells at cost; also consider the .ie) | ~€10-12/yr (.com), ~€25/yr (.ie) | you (payment) |
| 2 | Cloudflare account + Pages project (free, commercial use allowed) | €0 | you create; Claude configures |
| 3 | Anthropic API key with a monthly spend limit (start at €150) | ~€100-150/month in the pilot | you (payment); add as GitHub secret `ANTHROPIC_API_KEY` |
| 4 | Newsletter provider account (free tier at launch; verify the current subscriber limit) | €0 at launch | you; then add API key as a GitHub secret |
| 5 | Social handles on Instagram, TikTok, YouTube, LinkedIn, Bluesky (phone verification needed) | €0 | you |
| 6 | Company incorporation (CRO) | CRO fee, check current amount | you or an accountant |
| 7 | Solicitor review of `content/pages/` and an AI Act/defamation memo (get a fixed quote) | estimate €5-15k | you |
| 8 | Media liability insurance quotes | estimate €2-10k/yr | you |
| 9 | EU trade mark filing after clearance (EUIPO, classes 41, 9, 35) | €850 first class + extras | you or a trade mark attorney |

## C. Switch on (Claude can do all of this once A and B1-B4 are done)

1. Fill in `config/site.yaml`: brand, domain, company details, editor, newsletter form URL.
2. Repository settings → Actions → allow GitHub Actions to create pull requests.
3. Repository variables: `DEPLOY_TARGET=cloudflare`, `CLOUDFLARE_PROJECT=<name>`,
   `PIPELINE_ENABLED=true`, `NEWSLETTER_ENABLED=true` (when the list exists).
4. Secrets: `ANTHROPIC_API_KEY`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, newsletter key.
5. Run `Newsroom pipeline` by hand once; check `python -m gnn sources` output in the log and
   fix any feed that fails.
6. Review the first pull requests on your phone; merge the good ones; the site deploys.
7. Point the domain at Cloudflare Pages; check `https://<domain>/` loads with HTTPS.
8. Run `Progress trackers` once to load the data charts.

## D. Before announcing publicly

- Solicitor sign-off on the policy pages (they are marked as drafts for review).
- Company details and owners filled in on the ownership page (EMFA Art 6).
- 50-100 supervised stories published with no material corrections.
- Newsletter tested to yourself on Gmail, Outlook and Apple Mail.
