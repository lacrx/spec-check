# spec-check

A gate for the record a change carries about itself. It reads a commit message, a pull
request body or a spec file, and fails when the facts a reviewer needs are missing.

It judges **presence of facts, never style**. Terse prose and verbose prose carrying the same
facts both pass. A paragraph of filler fails and names the slot it is missing.

```
feat(ui): the map is the link, text link goes

Why: the answer carried a picture and a blue link to the same place.
Rule: the image src is never model-supplied; the url goes through the _url allowlist.
Verified: 390x844, hostile url drew unlinked, 0 off-origin images. Suite: 1067 passed.

Spec: docs/specs/2026-11-14-tappable-map.md
```

## Why it exists

One failure shape, over and over: the record of a change did not carry the fact that would
have caught the mistake.

- A capability is built, tested and merged **inert**, because nothing asks what makes it
  reachable.
- An incident is written down, and the writing is mistaken for the fix.
- A green tick means "the job ran" rather than "somebody looked".

A completion claim with no evidence attached is the thread running through all three. Git
history had no gate for it: a commit can claim anything, and `Verified:` shows up in some
bodies and not others.

## The slots

| Slot | What it holds |
|---|---|
| `Why:` | what was **observed**. Not what was feared. |
| `Rule:` | the invariant that is true now. |
| `Cost:` | the trade that was accepted, if any. |
| `Verified:` | the command and its result. `not run (<reason>)` is allowed, is never reported as verified, and cannot ship a tier-2 change. |
| `Rejected:` | what was turned down, and why. |
| `Spec:` | a path, `in-message`, or `none (<reason>)`. Required always. Ends with its own line. |

A slot that repeats the subject, or says fewer than three words, fails: the diff already says
what changed.

## Three tiers, by blast radius

| Tier | What | Needs |
|---|---|---|
| 0 | prose, prompts, docs: the class whose mistakes announce themselves | `Spec: none (<reason>)` |
| 1 | ordinary code | `Why:`, `Rule:`, `Verified:`; the message is the spec |
| 2 | the boundary class | a spec file, named in `Spec:` |

The tier-2 list is **read, not restated**, in this order:

1. `.spec-tier2` in the repository root: one glob per line, `#` for comments.
2. The `paths:` block of a review workflow (`--workflow`, default
   `.github/workflows/code-review.yml`). A repository that already filters an expensive
   review to its boundary class has written that list once, so widening the review widens
   what needs a spec, in one edit.
3. The built-in defaults: credentials, deployment, the CI configuration itself, and where
   authentication tends to live.

`spec-check tier` prints which source it used, because the tier is the part people argue with.

## The spec file

Five required sections and five front-matter keys.
[`examples/SPEC-TEMPLATE.md`](examples/SPEC-TEMPLATE.md) is the template, and the test suite
validates that file, so the example cannot drift from the rule.

The mechanism worth understanding: **every invariant needs a verification row.** An invariant
with no test, command or `manual:` observation against it fails the check. That is the same
rule as refusing a completion claim with no evidence, applied to the design rather than to
the commit.

## Use it in CI

```yaml
name: Spec gate
on:
  pull_request:
    types: [opened, synchronize, reopened, edited, ready_for_review]

jobs:
  spec-gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
        with:
          fetch-depth: 0          # the tier needs the base branch in the clone
      - uses: lacrx/spec-check@<commit-sha>
        with:
          body-file: ${{ runner.temp }}/pr-body.txt
          title: ${{ github.event.pull_request.title }}
          base: ${{ github.event.pull_request.base.ref }}
```

Write the body to a file through the **environment** first. A pull request title or body is
attacker-controlled text, and `${{ }}` inside a `run:` block is substituted before bash sees
the line, so a backtick in a body would execute on the runner:

```yaml
      - env:
          BODY: ${{ github.event.pull_request.body }}
        run: printf '%s' "${BODY:-}" > "${{ runner.temp }}/pr-body.txt"
```

Pin the action to a **commit sha**, not a tag or a branch. A tag can move.

`edited` belongs in the trigger list: the body is the record, and a body edited after the run
would otherwise keep a green tick earned by text that is no longer there.

## Use it locally

```bash
spec_check.py tier                        # what tier this change is, and why
spec_check.py msg .git/COMMIT_EDITMSG --warn
spec_check.py spec docs/specs/my-spec.md
spec_check.py pr --body-file body.txt --title "..."
spec_check.py --selftest                  # the cases that prove the rules
```

A `commit-msg` hook wants `--warn`: it reports and exits 0. Mid-branch commits are where a
thought is still moving, and a hook that hard-fails buys nothing except the `--no-verify`
habit. Fail at the pull request instead.

Exit codes: `0` pass, `1` fail with the missing slot named, `2` the tool could not run. That
third one matters: a tool error must never read as a verdict.

## Requirements

Python 3.9 or newer, standard library only. No install step, no dependencies, no network.

## License

MIT.
