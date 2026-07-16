# Independent implementation boundary

MEPPP is a new Django implementation with its own repository history, requirements, data model, routes, interface copy, tests, and assets. The current interface is an independent adaptation of publicly documented interaction and layout patterns; it does not vendor either reference project.

## Reviewed references

- **PaoPao CE** at commit `2f55b4b5d7f204d1b939b2ba41ccfde4b8e071cd` (MIT, Copyright 2022 ROC) is the primary interface reference. Review was limited to the public three-column information hierarchy, compact navigation, inline composer, border-separated stream, topic rail, and responsive collapse behaviour.
- **go-postery** at commit `1bcebf66c70bec6d12f0c2be812984cd69b5de57` (MIT, Copyright 2025 Zhi Lei Yang) is a secondary colour and contrast reference. MEPPP uses the common sky-blue `#0284c7` as its primary colour and independently defines the remaining tokens.

The exact references and attribution are recorded in [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md).

## Allowed inputs

- General community and microblog product patterns.
- Visual and behavioural review of public open-source interfaces whose licences permit inspection and adaptation.
- Neutral product requirements derived from that review.
- Independently written user stories, acceptance criteria, Django templates, CSS, tests, and interface copy.
- Public standards and official framework documentation.

## Excluded inputs

- Verbatim or mechanically translated source code, generated code, comments, tests, fixtures, or build output from a reference project.
- Copied DOM trees, CSS rules, source components, database schemas, migrations, API paths, internal identifiers, or configuration files.
- Reference brands, names, logos, icons, images, screenshots, sample/demo content, or distinctive interface copy.
- Git history, forks, submodules, vendored directories, or an upstream remote.

## Engineering rules

1. Describe each feature first as an independent MEPPP user story.
2. Implement it in MEPPP's existing Django architecture and use MEPPP-owned routes, models, migrations, copy, and assets.
3. Write tests from MEPPP acceptance criteria rather than translating reference-project tests.
4. Record the exact reference commit and licence when a public project materially informs the implementation.
5. Add a runtime dependency only through the package manager and retain its licence metadata.
6. Reject any contribution that cannot explain its independent source.

This document is an engineering separation policy, not legal advice.
