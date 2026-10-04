---
title: "Ownership and independence"
slug: "ownership-and-independence"
summary: "Who owns us, how we are funded, and how we keep our journalism independent of owners, advertisers and funders."
group: about
order: 3
updated: "04/10/2026"
legal_review_required: true
---

You should be able to see who is behind the news you read. Article 6 of the European Media Freedom Act (Regulation (EU) 2024/1083) requires media service providers to make information about their ownership and state funding easily and directly accessible. This page sets it out, and we keep it up to date.

## The publisher

- **Legal name:** {{ publisher.legal_name }}
- **Company number:** {{ publisher.company_number }} (Companies Registration Office, Ireland)
- **Registered address:** {{ publisher.registered_address }}
- **Contact:** {{ publisher.contact_email }}
- **Editor:** {{ publisher.editor_of_record }}

## Who owns us

The list below names every direct and indirect owner whose shareholding enables them to exercise influence over {{ brand.name }}, and every beneficial owner, with their role and shareholding.

{% for o in publisher.owners -%}
- {{ o.name }} ({{ o.role }}): {{ o.share }}
{% endfor %}

When ownership changes, we update this page.

## State advertising and public funds

Public funds for state advertising allocated to us, and advertising revenue received from public authorities or public bodies of countries outside the EU, in our last financial year: {{ publisher.state_advertising_revenue_last_year }}.

If we receive any other public funding, such as a grant under a journalism support scheme, we will list it here, with the funder and the amount.

## How we are funded

At launch, {{ brand.name }} is funded by its owners. If we accept income from any other source, such as advertising, sponsorship, grants or reader support, we will list the type of source here. All paid content follows our Sponsored content policy.

We do not accept funding from political parties, election candidates or campaigning organisations, and we do not accept any funding that comes with conditions about what we cover or how.

## Editorial independence

- The owners set the overall direction of the publication: verified, evidence-led reporting on progress, as described on our About page and in our Editorial standards.
- Within that direction, the editor decides what we publish, what we don't, and how stories are framed.
- Owners, directors, advertisers, sponsors and funders cannot direct individual editorial decisions. They do not see or approve stories before publication.
- If anyone tries to influence an individual editorial decision, the editor records it. We may report the attempt to readers.
- The editor will not be penalised for an editorial decision made in line with our standards.

## Conflicts of interest

We keep a register of interests. The owners, the directors, the editor and anyone else involved in editorial decisions declare:

- shareholdings and other financial interests;
- paid roles and directorships;
- membership of political parties or campaigning organisations;
- close personal or family relationships relevant to our coverage;
- gifts and hospitality beyond the trivial.

The register is updated at least once a year and whenever a declared interest changes. Anyone with a conflict steps back from the story concerned. Where that isn't possible, the story discloses the interest.

We publish a summary of the declarations relevant to our coverage on this page. Stories about our advertisers, sponsors or funders say so. Because we use AI in our work, stories about our AI provider, currently Anthropic, will disclose that relationship too.

## Questions

If you have a question about our ownership, funding or independence, email {{ publisher.contact_email }}.
