---
id: 2026-11-14-extracted-as-an-action
tier: 2
status: shipped
quiet: yes
paths:
  - spec_check.py
  - action.yml
  - tests/test_spec_check.py
  - examples/SPEC-TEMPLATE.md
  - .github/workflows/ci.yml
---

# One implementation, reachable from any repository's CI

This tool was born inside one private repository, as `scripts/spec_check.py`, because a
GitHub Actions runner cannot read a private notes tree and the alternative at the time was a
second copy. That worked for one repository and does not generalise: every other repository
would have needed a copy, and N copies of a rule is the failure this tool exists to catch.

## Problem

- **CI cannot reach a private source.** The canonical copy lived in the repository whose CI
  ran it, so a second repository could only get it by copying, and a copy cannot be checked
  against the original from inside a runner: there is no credential for the original and
  there should not be one.
- **A private action would mean a new long-lived credential in every consuming repository.**
  Checking out a private repository from another repository's workflow needs a token, stored
  as a secret, one per consumer. The deployment this came from spends real effort keeping
  credentials out of files that get backed up thirty times; adding N of them to GitHub to
  distribute a *linter* is the wrong trade.
- **The documented fallback did not exist.** The docstring promised a built-in tier-2 list
  for repositories with no review workflow to read; `boundary_globs` returned an empty list,
  which made every change in such a repository tier 1 at most. Invisible where it was born,
  because that repository has a review workflow. Silently wrong everywhere else, in the
  direction that matters: a credential change would have needed no spec.

## Invariants

- **S1** WHEN this action runs in any repository, it SHALL use this repository's
  `spec_check.py` and no vendored copy.
- **S2** WHERE a repository states no tier-2 list, the built-in defaults SHALL apply, and
  `tier` SHALL name which of the three sources it used.
- **S3** WHEN a repository ships `.spec-tier2` and a review workflow both, the tier-2 list
  SHALL be their union, and a file in the checkout SHALL NOT be able to remove a glob that
  the review workflow names. The built-in defaults SHALL apply only where a repository names
  neither.
- **S4** WHEN a spec path lies outside the repository, it SHALL be accepted unvalidated only
  under a prefix the deployment configured, and SHALL otherwise fail as a missing file.
- **S5** WHEN a consumer pins this action, it SHALL pin a commit sha rather than a tag, so a
  moved tag cannot change what runs in a gate.
- **S6** The template every consumer copies SHALL itself pass `spec-check spec`.

## Blast radius

- **This is a public repository holding a gate other repositories depend on**, so a bad commit
  on `main` reaches every consumer that moves its pin, and a force-push could change what a
  pinned sha means for nobody, since a sha is the content. Pinning by sha is what bounds the
  radius: a consumer only takes a change when it edits its own pin.
- **A mistake here is quiet in one specific way**: this gate fails closed on a missing record
  and open on a *wrong* record, because nothing mechanical can read whether a `Why:` is true.
  What makes it loud is a human or model reviewer answering the five questions in the README;
  the gate is the floor, not the ceiling.
- The content is deliberately generic: no private paths, no incident numbers, nothing about
  the house it came from. Its reasoning survives as failure shapes, which is the part that
  generalises anyway.

## Verification

| Invariant | How it is proved |
|---|---|
| S1 | `manual:` the consumer workflows `uses: lacrx/spec-check@<sha>` and carry no copy of the tool; the first consumer's gate ran green against this repository's file. |
| S2 | `TestTier::test_the_built_in_list_covers_a_repository_with_nothing_of_its_own` and `test_the_source_is_named_in_the_explanation`, plus three `--selftest` cases. |
| S3 | `TestTier::test_a_repositorys_own_file_adds_to_the_workflow_and_never_replaces_it`, `test_a_branch_cannot_demote_itself_by_adding_a_file` (asserts the union is a superset), `test_duplicates_across_the_two_sources_collapse`, `test_the_workflow_replaces_the_defaults`. |
| S4 | `TestTheToolItself::test_a_spec_outside_the_repository_needs_the_prefix_configured`, both directions. |
| S5 | `manual:` every consumer workflow was written with a sha, and the README says why. Nothing enforces it mechanically, which is stated rather than implied. |
| S6 | `.github/workflows/ci.yml` runs `spec_check.py spec examples/SPEC-TEMPLATE.md` on every push. |
| The suite | `python3 -m pytest tests/ -q`: 68 passed. `python3 spec_check.py --selftest`: 24/24. CI runs both on 3.9 and 3.13, the 3.9 leg on `ubuntu-22.04` because `setup-python` has no 3.9 build for Ubuntu 26.04. `manual:` the selftest and the template check also run green on macOS's own `/usr/bin/python3`, 3.9.6, which is what a `commit-msg` hook there would use. |

## Rejected

- **Vendoring a copy into each repository.** No network at CI and no secret, but N copies of
  one fact, and CI cannot detect drift between them: only a host-side timer could, so
  divergence stays quiet until the next tick.
- **A private repository plus a fine-grained token per consumer.** One implementation and
  nothing public, at the cost of a new long-lived credential at rest in every repository's
  secrets, to distribute a linter.
- **A PyPI package.** `pip install` in every workflow, a release process, and a supply chain
  wider than a single file needs. The whole tool is one stdlib module on purpose.
- **A git submodule per consumer.** Checkout flags and update discipline in every repository,
  for the same result a pinned action gives with none of it.
- **A precedence order between the three sources**, which is what shipped first and was
  wrong. The checker runs against the pull request's own checkout, so the branch supplies
  the configuration: a branch could edit a sensitive path, add a `.spec-tier2` naming one
  unrelated glob, and grade itself tier 1 with no spec required. Found by the reviewer on
  the first pull request that consumed this repository as an action. A union has the
  property that matters, that a file in the branch can only ever widen the list, and keeps
  the capability that made the file worth having.
- **Keeping the household's incident narrative in the comments.** The reasoning is what makes
  the rules stick, so it stays; the specific files, pull request numbers and people do not
  belong in a public repository and are no loss, because what generalises is the *shape* of
  each failure.
