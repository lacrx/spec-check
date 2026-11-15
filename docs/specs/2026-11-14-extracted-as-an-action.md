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
- **S7** WHEN a documentation file carries a `spec-check:rules` block, `docs --check` SHALL
  fail IF that block differs from what this version of the tool emits, and the block SHALL
  be derived from the constants the enforcement reads rather than written by hand.
- **S8** WHEN a file named to `docs --check` carries no block, or a phrasing marked
  `retired:` appears elsewhere in it, THEN the check SHALL fail rather than pass quietly.

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
| S7 | `test_the_block_names_every_slot_and_tier` asserts every slot, every tier level, **every `TIER0_GLOBS` entry** and every `DEFAULT_TIER2` entry appears. That tier-0 assertion exists because the first version emitted half the tier model: a consumer replaced its hand-written tier table with the block and thereby deleted the only statement of which paths are tier 0. A generated block that silently covers less than the thing it replaced is worse than the prose, because nobody re-reads generated text. `TestGeneratedDocs::test_a_block_goes_stale_when_the_rules_change` monkeypatches `REQUIRED_SLOTS` and asserts the block goes red, which is the inversion that matters; `test_the_block_names_every_slot_and_tier` and `test_the_block_carries_no_version_or_sha` cover what it must and must not contain. This repository's own README carries a block and its CI checks it. |
| S8 | `TestGeneratedDocs::test_a_file_with_no_block_fails_rather_than_passing`, `test_an_unclosed_block_is_a_failure_not_a_silent_pass`, `test_retired_phrasing_coming_back_fails`, `test_a_retired_directive_alone_is_not_a_failure`, and `test_retired_phrasing_is_caught_however_it_is_wrapped` (four wrappings). The comparison is whitespace-collapsed and case-folded: it was a raw substring test, and the first file to carry a directive reproduced the retired sentence across a line break while the check reported OK. `test_a_paraphrase_of_a_retired_phrase_is_allowed` holds the other side, so the history of a retired claim stays writable. |
| The flag its own documents described | `TestGeneratedDocs::test_check_is_the_default_and_the_explicit_flag_works`. `docs --check` was in the README, the action and this repository's CI before it was in `argparse`; CI failed with `error: unrecognized arguments: --check` on the first run. Both forms work now, and `--check --write` together is a tool error rather than a guess. |
| The noise floor | `TestMessage::test_generated_and_in_flight_subjects_are_left_alone` covers GitHub's synthetic `Merge <sha> into <sha>`, which is HEAD in a `pull_request` checkout and was warning about a missing `Why:` on every pull request in every repository. `test_an_ordinary_subject_is_never_mistaken_for_a_generated_one` holds the other side with a subject that names one sha and is authored prose. |
| The suite | `python3 -m pytest tests/ -q`: 90 passed. `python3 spec_check.py --selftest`: 24/24. CI runs both on 3.9 and 3.13, the 3.9 leg on `ubuntu-22.04` because `setup-python` has no 3.9 build for Ubuntu 26.04. `manual:` the selftest and the template check also run green on macOS's own `/usr/bin/python3`, 3.9.6, which is what a `commit-msg` hook there would use. |

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
- **An LLM pass over the prose to find contradictions.** It is the only thing that catches
  two hand-written paragraphs disagreeing in nuance, and a reviewer already does it well:
  three for three on the change that prompted this. What it cannot be is a *gate*, because
  it is probabilistic, so the mechanical half is generated blocks and retired phrasings and
  the judgement half stays with the reviewer.
- **Generating whole sections rather than a block.** The reasoning and the incidents are the
  part worth reading and the part no tool can emit. Only the facts the code owns are
  generated, which is why the marker delimits a block inside a hand-written section.
- **Embedding the tool's sha in the generated block.** Every commit here would then make
  every consumer's documentation stale, and a check that fires when nothing is wrong is one
  everybody learns to re-run blindly. Asserted by `test_the_block_carries_no_version_or_sha`.
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
