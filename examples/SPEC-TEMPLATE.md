---
id: 2026-01-01-example-change
tier: 2
status: proposed
quiet: yes
paths:
  - src/auth/session.py
  - .github/workflows/deploy.yml
---

# A one-line title that says what changes, not what is wrong

This file is both the template to copy and the fixture `tests/test_spec_check.py` validates,
so a change to what a spec must contain cannot pass while the example of one fails.

Front matter, all five keys required:

- `id` matches the filename, which is `<date>-<slug>.md`.
- `tier` is 0, 1 or 2. `spec-check tier` computes it; this records what it said.
- `status` is `proposed`, `accepted`, `shipped` or `abandoned`.
- `quiet` answers "does a mistake here stay quiet", yes or no. Answering yes is not a reason
  to abandon the change; it decides what the Blast radius section has to name.
- `paths` is what the change touches, so a reader sees the blast radius before the prose.

## Problem

What was **observed**, with evidence: a command and its output, a date, a `file:line`. Not
what was feared, and not a feature request phrased as a defect.

A worked example, from the repository this tool came from: a capability was built, tested and
merged inert three times in two days, because the prose that tells the model it exists was
never written. Nothing in the process asked "what makes this reachable", so nothing caught it
the second or third time either.

Write the evidence down even when it is embarrassing or obvious. A problem statement that
cannot be checked is a problem statement that can be wrong forever.

## Invariants

Numbered, falsifiable, EARS-shaped (WHEN / WHILE / IF plus SHALL). Anything that cannot be
shown false is a hope, not an invariant.

- **S1** WHEN a session is older than `SESSION_TTL`, the server SHALL refuse it and SHALL log
  the refusal with the account id and never the token.
- **S2** WHILE a refresh is in flight, a second refresh for the same account SHALL NOT start.
- **S3** IF the store cannot be read, THEN nobody SHALL count as signed in, rather than
  everybody.

## Blast radius

What a mistake costs, who notices, and when. Name the person or the process that finds out,
because "it would show up eventually" is the sentence that lets a defect live for months.

If `quiet: yes`, this section has to say what makes it loud: a test, an alert, a red check, a
line in a log somebody reads. A quiet failure with no detector named is the finding, not the
footnote.

## Verification

One row per invariant. A test name, a command with its result, or `manual:` with what was
done and what was seen. An invariant with no row fails `spec-check spec`, which is the whole
mechanism: it is the same rule as refusing a completion claim with no evidence attached.

| Invariant | How it is proved |
|---|---|
| S1 | `tests/test_session.py::test_expired_session_is_refused`, and the log line asserted to hold the account id and no token. |
| S2 | `tests/test_session.py::test_one_refresh_at_a_time`, 50 threads, one login attempt observed. |
| S3 | `manual:` the store was made unreadable with `chmod 000`, the app answered "not signed in" and the first good read restored it. |

`not run` is allowed where it is honest, and says why: `manual: not run (no hardware in CI)`.
It is never reported as verified, and a tier-2 change cannot ship with it.

## Rejected

What was turned down, and why, or the literal `none considered`. This section is the one that
saves the next person a week, because an approach that looks obvious from the outside usually
failed for a reason nobody wrote down.

- A global switch instead of a per-account one: the state it would share is exactly the state
  that must not be shared.
- A second copy of the list, kept in sync by hand: a fact in two places is a fact that can
  disagree with itself.

## Plan

Optional, and useful while the change is still ahead of you. Ordered steps, each naming what
proves it. Delete it or leave it as the record of what was done.
