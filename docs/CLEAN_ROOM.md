# Independent implementation boundary

MEPPP is a new implementation with its own repository history, requirements, data model, routes, interface copy, tests, and assets.

## Allowed inputs

- General community and microblog product patterns.
- High-level visual and behavioural review of public open-source interfaces, rewritten as neutral product requirements.
- Independently written user stories and acceptance criteria.
- Public standards and official framework documentation.
- Behaviour observed at a high level, rewritten as neutral product requirements.

## Excluded inputs

- Source code, generated code, comments, tests, fixtures, or build output from a reference project.
- Database schemas, migrations, API paths, internal identifiers, or configuration files from a reference project.
- Interface text, documentation prose, names, logos, icons, images, design tokens, or other distinctive assets from a reference project.
- Git history, forks, submodules, vendored directories, or an upstream remote.

## Engineering rules

1. Describe each feature first as an independent user story.
2. Use MEPPP-owned domain names and migrations.
3. Write tests from MEPPP acceptance criteria.
4. Add a dependency only through the package manager and retain its license metadata.
5. Reject any contribution that cannot explain its independent source.

This document is an engineering separation policy, not legal advice.
