#!/usr/bin/env python3
"""Check that a change says enough about itself to be reviewed.

WHAT THIS IS FOR
================

One failure shape, seen over and over: the record of a change did not carry the fact that
would have caught the mistake.

- A capability is built, tested and merged **inert**, because nothing in the process asks
  what makes it reachable. Three times in two days, in the repository this came from.
- An incident is written down and the writing is mistaken for the fix. One of those sat live
  for eight months under a paragraph describing it in the past tense.
- A green tick means "the job ran" rather than "somebody looked", and goes green over a file
  with known defects.

A completion claim with no evidence attached is the common thread. Git history had no gate
for it: a commit can claim anything, and `Verified:` appeared in some bodies and not others.
This is that gate, for commit messages, pull request bodies and spec files.

WHAT IT DOES NOT DO
===================

It judges the PRESENCE OF FACTS and never length, wording or style. Terse prose and verbose
prose carrying the same facts both pass; a paragraph of filler with no facts fails. A gate
that fires when nothing is wrong is one you learn to bypass, and a bypassed gate is worse
than no gate: it still shows green. So every failure names the missing slot, and there is a
documented way to say "this change needs no spec" that passes.

THE TIER LIST IS NOT WRITTEN DOWN TWICE
=======================================

A change is tier 0, 1 or 2 by what it touches, and the tier-2 list is **read** rather than
restated. It is the UNION of:

1. `.spec-tier2` in the repository root, one glob per line, `#` for comments.
2. The `paths:` block of a review workflow (`--workflow`, default
   `.github/workflows/code-review.yml`). A repository that already filters an expensive
   review to its boundary class has written the list once; this reads that copy, so widening
   the review widens what needs a spec in one edit.

`DEFAULT_TIER2` below applies only when a repository names neither.

**A union and not a precedence order, deliberately.** This runs against the pull request's
own checkout, so the branch supplies the configuration; under precedence a branch could add
a `.spec-tier2` naming one unrelated glob and demote its own sensitive change to tier 1.
Under a union, a repository's own file can only ever make MORE paths tier 2.

A fact kept in two places is a fact that can disagree with itself. `tier` prints which
sources it used, because the tier is the part people argue with.

USAGE
=====

    spec_check.py tier [--base origin/main]      what tier this change is, and why
    spec_check.py msg <file|-> [--warn]          check a commit message
    spec_check.py spec <path>                    check a spec file
    spec_check.py pr --body-file <f>             check a pull request body (and its spec)
    spec_check.py --selftest                     run the cases that prove the rules

Exit codes: 0 pass, 1 fail (with the missing slot named), 2 the tool itself could not run.
`--warn` reports failures and exits 0, which is what the commit-msg hook uses.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

WORKFLOW = ".github/workflows/code-review.yml"
TIER2_FILE = ".spec-tier2"

# Paths whose mistakes announce themselves the next time somebody runs the thing: prompt
# text, prose, documentation. The test is not "is it important" but "does a mistake stay
# quiet", and a broken sentence in a prompt or a README is loud.
TIER0_GLOBS = (
    "app/prompts/*",
    "docs/*",
    "*.md",
    "LICENSE",
    ".gitignore.example",
)

SLOTS = ("Why", "Rule", "Cost", "Verified", "Rejected", "Spec")

# What each tier must carry, by tier level. **Read by the enforcement and by
# `rules --markdown`**, so documentation generated from it cannot describe a rule this file
# does not apply. That is the whole point of generating it: prose restating a rule is prose
# that can rot, and nothing goes red when it does.
REQUIRED_SLOTS = {
    0: ("Spec",),
    1: ("Why", "Rule", "Verified", "Spec"),
    2: ("Why", "Rule", "Verified", "Spec"),
}

# One line each, for the generated table. The enforcement does not read these, which is the
# one place a drift is still possible here: a slot's MEANING is prose. Its presence,
# its tier and its minimum content are all generated from the code above.
SLOT_MEANING = {
    "Why": "what was **observed**, not what was feared",
    "Rule": "the invariant that is true now",
    "Cost": "the trade that was accepted, if any",
    "Verified": "the command and its result",
    "Rejected": "what was turned down, and why",
    "Spec": "a path, `in-message`, or `none (<reason>)`",
}

# `Spec:` names a path and nothing else, so it ends with its own line. Everything else may
# run on over wrapped prose, a list or a table.
SINGLE_LINE_SLOTS = ("Spec",)

# A trailer is `Key: value` too, so the slot parser has to know which keys are not slots or a
# `Co-Authored-By` line would be read as the continuation of whatever came before it.
TRAILER_KEYS = (
    "Co-Authored-By",
    "Co-authored-by",
    "Signed-off-by",
    "Closes",
    "Refs",
    "Fixes",
    "Reviewed-by",
    "Reported-by",
    "BREAKING CHANGE",
)

SPEC_SECTIONS = ("Problem", "Invariants", "Blast radius", "Verification", "Rejected")
SPEC_KEYS = ("id", "tier", "status", "quiet", "paths")
SPEC_STATUSES = ("proposed", "accepted", "shipped", "abandoned")

# Subjects that are not authored prose: git generated them, or a rebase has not happened yet.
#
# **Matched as one anchored pattern, on a word boundary, which it was not at first.** The
# prefixes were a `str.startswith` tuple holding `wip`, so `Wipe stale mapshots on startup`
# was read as a work-in-progress marker and skipped the whole check: an empty body passed
# `spec-gate` at tier 2 and auto-merge then read that green tick as the record being present.
# `SKIP_SUBJECTS` was matched with `in`, anywhere in the line, so `Document why merge branch
# fails` was skipped too. A gate that waves a change through on a word in its title is worse
# than no gate, and both forms were found by the reviewer on this file's own pull request.
#
# Every one of these is generated at the START of a subject, so anchoring costs nothing.
SKIP_RE = re.compile(
    r"^(?:fixup!|squash!|amend!|wip\b|merge pull request\b|merge branch\b"
    r"|merge remote-tracking\b|revert \""
    # **GitHub's own synthetic merge commit**, `Merge <sha> into <sha>`, which is what a
    # `pull_request` checkout puts at HEAD. It was not matched, so the commit-message loop
    # warned about a missing `Why:` on every pull request in every repository: noise, in a
    # report whose only value is that somebody still reads it. Two shas and nothing else, so
    # it cannot collide with a hand-written subject.
    r"|merge [0-9a-f]{7,40} into [0-9a-f]{7,40}$)",
    re.IGNORECASE,
)

MAX_SUBJECT = 72
MIN_SLOT_WORDS = 3


class ToolError(Exception):
    """The check could not be run. Exit 2, never 1: this is not a verdict."""


# --------------------------------------------------------------------------------------- git


def repo_root(start: Path | None = None) -> Path:
    out = run(["git", "rev-parse", "--show-toplevel"], cwd=start or Path.cwd())
    return Path(out.strip())


def run(cmd: list[str], cwd: Path | None = None) -> str:
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, check=False, timeout=60
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - environment
        raise ToolError(f"{cmd[0]} could not run: {exc}") from exc
    if proc.returncode != 0:
        raise ToolError(f"{' '.join(cmd)} failed: {proc.stderr.strip()}")
    return proc.stdout


# The tier-2 list for a repository that states none of its own: credentials, deployment, the
# CI configuration itself, and the places authentication tends to live. Each is here by the
# "does a mistake stay quiet" test rather than because the file looks important.
#
# **This exists because the fallback was documented before it was written.** The first version
# of this tool said in its own docstring that it "falls back to a built-in list for
# repositories without one", and `boundary_globs` returned an empty list, which made every
# change in such a repository tier 1 at most. Nothing was wrong where the tool was born, since
# that repository has a review workflow; it would have been silently wrong everywhere else, in
# exactly the direction that matters.
DEFAULT_TIER2 = (
    ".github/workflows/**",
    ".github/actions/**",
    ".env*",
    "**/.env*",
    ".gitignore",
    "Dockerfile*",
    "**/Dockerfile*",
    "docker-compose*.yml",
    "deploy/**",
    "infra/**",
    "terraform/**",
    "*.tf",
    "**/auth*.py",
    "**/*_auth.py",
    "**/config.py",
    "**/settings.py",
    "**/secret*",
    "**/credential*",
    "**/iam*",
)


def tier2_from_file(root: Path) -> list[str]:
    """`.spec-tier2`: one glob per line, `#` comments, blank lines ignored.

    For a repository with no expensive review to borrow a list from, and a boundary worth
    naming anyway: the one that can spend money, or holds a token, or deploys something.
    """
    path = root / TIER2_FILE
    if not path.is_file():
        return []
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def tier2_globs(root: Path, workflow: str = WORKFLOW) -> tuple[list[str], str]:
    """The tier-2 globs and WHERE THEY CAME FROM. **A UNION, never a precedence order.**

    The first version of this took the first source that answered: `.spec-tier2` if present,
    else a review workflow's `paths:`, else the built-in defaults. A reviewer found what that
    allows, and it is the worst thing a gate can do.

    This runs against the pull request's own checkout, so the BRANCH supplies the
    configuration. Under precedence, a branch could edit a sensitive file and add a
    `.spec-tier2` naming one unrelated glob: its own file would win, the change would be
    graded tier 1, no spec file would be required, and a clean review would merge it. The
    thing being gated got to choose the gate's settings, in the same change, with nothing
    going red.

    A union cannot do that. Adding a file, or adding a line to one, can only ever make MORE
    paths tier 2. The only way to narrow the list is to delete entries that are already
    there, which is a visible edit to a tracked file that the list itself can cover.

    The built-in defaults apply only when a repository names nothing of its own, because
    those are a floor for repositories with no opinion rather than an addition to one that
    has expressed theirs.
    """
    named = tier2_from_file(root)
    listed = boundary_globs(root, workflow)
    if named and listed:
        # Deduplicated, order preserved: the file first, since that is the one somebody in
        # this repository wrote on purpose.
        merged = list(dict.fromkeys([*named, *listed]))
        return merged, f"{TIER2_FILE} + {workflow} paths:"
    if named:
        return named, TIER2_FILE
    if listed:
        return listed, f"{workflow} paths:"
    return list(DEFAULT_TIER2), "built-in defaults"


def boundary_globs(root: Path, workflow: str = WORKFLOW) -> list[str]:
    """The quoted globs under `paths:` in the reviewer's workflow.

    Comments inside the block are skipped rather than parsed: they explain the entries, and
    they are most of the file. The scan stops at the first line that is neither a list item
    nor a comment. A test gate in the same repository can parse the same block the same way,
    which is the point of reading it rather than copying it.
    """
    path = root / workflow
    if not path.is_file():
        return []
    globs: list[str] = []
    inside = False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not inside:
            if stripped == "paths:":
                inside = True
            continue
        if stripped.startswith("#") or not stripped:
            continue
        if stripped.startswith("- "):
            globs.append(stripped[2:].strip().strip("'\""))
            continue
        break
    return globs


def changed_files(root: Path, base: str) -> list[str]:
    """What a merge would bring in, plus uncommitted and staged work."""
    files: list[str] = []
    have_base = (
        subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", base],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        ).returncode
        == 0
    )
    if have_base:
        files += run(["git", "diff", "--name-only", f"{base}...HEAD"], cwd=root).split()
    files += run(["git", "diff", "--name-only", "HEAD"], cwd=root).split()
    files += run(["git", "diff", "--name-only", "--cached", "HEAD"], cwd=root).split()
    return sorted({f for f in files if f})


def matches(path: str, glob: str) -> bool:
    """`**` is Actions for "any depth"; `fnmatch`'s `*` already crosses `/`, so the two
    collapse to the same pattern, which is also what a bash `[[ $file == $pattern ]]` does
    with the same substitution."""
    return fnmatch.fnmatch(path, glob.replace("**", "*"))


@dataclass
class Tier:
    level: int
    reasons: list[str] = field(default_factory=list)
    source: str = ""

    def explain(self) -> str:
        where = f" [from {self.source}]" if self.source and self.level == 2 else ""
        if self.level == 2:
            return "tier 2 (boundary class)" + where + ": " + "; ".join(self.reasons)
        if self.level == 1:
            return "tier 1 (behaviour change): " + "; ".join(self.reasons)
        return "tier 0 (announces itself): " + "; ".join(self.reasons or ["no files changed"])


def tier_for(files: list[str], globs: list[str], source: str = "") -> Tier:
    boundary = [
        f"{f} matches {g}" for f in files for g in globs if matches(f, g)
    ]
    if boundary:
        return Tier(2, boundary, source)
    code = [f for f in files if not any(matches(f, g) for g in TIER0_GLOBS)]
    if code:
        return Tier(1, [", ".join(code[:6]) + (" ..." if len(code) > 6 else "")])
    return Tier(0, [", ".join(files[:6]) + (" ..." if len(files) > 6 else "")] if files else [])


# ----------------------------------------------------------------------------------- messages


@dataclass
class Message:
    subject: str
    slots: dict[str, str]
    skip: bool = False


def parse_message(text: str) -> Message:
    """Pull the subject and the slot contents out of a commit message or pull request body.

    Git comment lines and everything below the scissors are dropped first, because the
    commit-msg hook is handed the template rather than the message a human typed.
    """
    lines: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.startswith("# ------------------------ >8"):
            break
        if line.startswith("#"):
            continue
        lines.append(line)

    while lines and not lines[0].strip():
        lines.pop(0)
    subject = lines[0].strip() if lines else ""
    skip = bool(SKIP_RE.match(subject))

    slots: dict[str, list[str]] = {}
    current: str | None = None
    slot_re = re.compile(rf"^({'|'.join(SLOTS)}):\s*(.*)$")
    trailer_re = re.compile(rf"^({'|'.join(re.escape(k) for k in TRAILER_KEYS)}):")
    fence = False
    body = lines[1:]
    for index, raw in enumerate(body):
        if raw.lstrip().startswith("```"):
            fence = not fence
        if not fence:
            hit = slot_re.match(raw)
            if hit:
                current = hit.group(1)
                slots.setdefault(current, []).append(hit.group(2).strip())
                # **`Spec:` is a trailer and ends with its own line.** It was a multi-line
                # slot like the rest until the first real pull request body, where it was
                # followed by a blank line and
                # the "Generated with Claude Code" attribution; those were read as the
                # continuation of the path, and the gate said
                # `docs/specs/2026-11-14-spec-rules.md\n\n🤖 Generated with...` did not exist
                # in the repository. The path was there. The parser had eaten the rest of the
                # body with it, and the message named the wrong problem, which is the failure
                # mode this whole file is against.
                if current in SINGLE_LINE_SLOTS:
                    current = None
                continue
            if trailer_re.match(raw):
                current = None
                continue
            # A blank line ends a slot unless what follows belongs to it: an indented
            # continuation, a list item, or a table row. That is what lets `Rule:` own the
            # table under it while the closing paragraph of a body stays out of `Verified:`.
            if current is not None and not raw.strip():
                nxt = next((line for line in body[index + 1 :] if line.strip()), "")
                if not (nxt.startswith((" ", "\t")) or nxt.lstrip().startswith(("-", "*", "|", ">"))):
                    current = None
                    continue
        if current is not None:
            slots[current].append(raw.strip())
    return Message(
        subject=subject,
        slots={k: "\n".join(v).strip() for k, v in slots.items()},
        skip=skip,
    )


def words(value: str) -> int:
    return len([w for w in re.split(r"\s+", value.strip()) if w])


# A path wrapped in backticks is still that path.
BACKTICKED = re.compile(r"^`+\s*(.+?)\s*`+$")


def unquote(value: str) -> str:
    """Drop the markdown quoting around a slot value before anything compares it.

    This gate's own message says "Write one under docs/specs/ and name it here", and the body
    of a pull request is rendered markdown, so anybody naming a path there writes it in
    backticks. The first body that did got ``Spec: `docs/specs/<name>.md`` does not exist in
    this repository`` about a file that was right there in the branch: the path was correct
    and the parser had kept its presentation, then compared that to the filesystem.

    Same family as the `Spec:` trailer bug in `parse_message` one function up, and the same
    lesson: strip what is formatting before the comparison, or a red check names the wrong
    problem and the next person debugs the file instead of the gate.
    """
    hit = BACKTICKED.match(value.strip())
    return hit.group(1) if hit else value.strip()


@dataclass
class Spec:
    value: str

    @property
    def kind(self) -> str:
        if self.value.startswith("none"):
            return "none"
        if self.value.strip() == "in-message":
            return "in-message"
        return "path"


def check_message(
    text: str, tier: Tier, *, root: Path | None = None, where: str = "commit message"
) -> list[str]:
    """Every rule that applies to a commit message or a pull request body.

    Returns the failures, each naming the slot that is missing, so a red check says what to
    type rather than that something is wrong.
    """
    msg = parse_message(text)
    if msg.skip:
        return []
    fails: list[str] = []

    if not msg.subject:
        return [f"{where}: empty"]
    if len(msg.subject) > MAX_SUBJECT:
        fails.append(
            f"subject is {len(msg.subject)} characters, cap is {MAX_SUBJECT}: "
            f"{msg.subject[:50]}..."
        )
    if msg.subject.endswith("."):
        fails.append("subject ends with a period")

    spec_raw = unquote(msg.slots.get("Spec", ""))
    if not spec_raw:
        fails.append(
            "no `Spec:` trailer. One of: `Spec: <path to spec file>`, `Spec: in-message`, "
            "`Spec: none (<reason>)`"
        )
        spec = None
    else:
        spec = Spec(spec_raw)
        if spec.kind == "none" and not re.match(r"^none\s*\(.+\)\s*$", spec_raw):
            fails.append("`Spec: none` needs a reason in brackets: `Spec: none (<reason>)`")

    required = REQUIRED_SLOTS[tier.level]
    for slot in required:
        if slot == "Spec":
            continue
        value = msg.slots.get(slot, "").strip()
        if not value:
            fails.append(f"no `{slot}:` line ({tier.explain()})")
            continue
        if words(value) < MIN_SLOT_WORDS:
            fails.append(f"`{slot}:` says almost nothing: {value!r}")
        elif value.strip().lower() == msg.subject.lower():
            fails.append(f"`{slot}:` repeats the subject, which the diff already says")

    verified = msg.slots.get("Verified", "").strip()
    if verified.lower().startswith("not run"):
        if not re.match(r"^not run\s*\(.+\)", verified, re.IGNORECASE):
            fails.append("`Verified: not run` needs a reason: `not run (<reason>)`")
        if tier.level == 2:
            fails.append(
                "tier 2 cannot ship unverified. Run it, or say what you did run and saw"
            )

    if tier.level == 2 and spec is not None:
        if spec.kind != "path":
            fails.append(
                f"{tier.explain()} needs a spec file, not `Spec: {spec_raw}`. "
                "Write one under docs/specs/ and name it here"
            )
        elif root is not None:
            fails += check_spec_reference(root, spec_raw)
    return fails


def external_prefixes() -> tuple[str, ...]:
    """Path prefixes whose specs live outside this repository, from `SPEC_EXTERNAL_PREFIX`.

    Some changes have no repository of their own: machine state, a workflow rule that spans
    several repositories, a decision recorded in a private notes tree. Their specs live
    wherever that is, which a CI runner usually cannot read and must not pretend to.

    Empty by default, so the ordinary case is "a spec file in this repository or no spec".
    A deployment that keeps specs elsewhere sets the prefix once, in the gate.
    """
    raw = os.environ.get("SPEC_EXTERNAL_PREFIX", "")
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def check_spec_reference(root: Path, ref: str) -> list[str]:
    """A spec under a configured external prefix is accepted and NOT validated here."""
    prefixes = external_prefixes()
    if prefixes and ref.startswith(prefixes):
        print(f"note: {ref} is outside this repository and was not validated here")
        return []
    path = root / ref
    if not path.is_file():
        return [f"`Spec: {ref}` does not exist in this repository"]
    return [f"{ref}: {f}" for f in spec_failures(path)]


# -------------------------------------------------------------------------------- spec files


def parse_front_matter(text: str) -> dict[str, object]:
    """The `key: value` and `- item` subset of YAML, which is all a spec header uses.

    Deliberately not PyYAML: this has to run on a bare runner and on both machines with no
    install step, the same constraint every tool in `claude-brain/tools` works under.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        raise ToolError("no front matter: the file must open with a `---` line")
    out: dict[str, object] = {}
    key: str | None = None
    for line in lines[1:]:
        if line.strip() == "---":
            return out
        if line.startswith(("  - ", "- ")):
            if key is None:
                raise ToolError(f"list item with no key: {line.strip()}")
            cur = out.setdefault(key, [])
            if isinstance(cur, list):
                cur.append(line.strip()[2:].strip())
            continue
        hit = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if hit:
            key = hit.group(1)
            value = hit.group(2).strip()
            out[key] = value if value else []
    raise ToolError("front matter is not closed with a `---` line")


def sections(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    name: str | None = None
    buf: list[str] = []
    for line in text.split("\n"):
        if line.startswith("## "):
            if name is not None:
                out[name] = "\n".join(buf)
            name = line[3:].strip()
            buf = []
            continue
        buf.append(line)
    if name is not None:
        out[name] = "\n".join(buf)
    return out


def spec_failures(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    fails: list[str] = []
    try:
        head = parse_front_matter(text)
    except ToolError as exc:
        return [str(exc)]

    for key in SPEC_KEYS:
        if key not in head or (isinstance(head[key], str) and not head[key].strip()):
            fails.append(f"front matter has no `{key}`")
    status = str(head.get("status", "")).strip()
    if status and status not in SPEC_STATUSES:
        fails.append(f"`status: {status}` is not one of {', '.join(SPEC_STATUSES)}")
    tier = str(head.get("tier", "")).strip()
    if tier and tier not in ("0", "1", "2"):
        fails.append(f"`tier: {tier}` is not 0, 1 or 2")
    quiet = str(head.get("quiet", "")).strip().lower()
    if quiet and quiet not in ("yes", "no"):
        fails.append("`quiet` answers 'does a mistake here stay quiet': yes or no")
    if not head.get("paths"):
        fails.append("front matter has no `paths` list")

    found = sections(text)
    lowered = {k.lower(): v for k, v in found.items()}
    for name in SPEC_SECTIONS:
        if name.lower() not in lowered:
            fails.append(f"no `## {name}` section")

    invariants = lowered.get("invariants", "")
    verification = lowered.get("verification", "")
    ids = sorted(set(re.findall(r"\bS(\d+)\b", invariants)), key=int)
    if not ids:
        fails.append("the Invariants section states no numbered `S1`, `S2`, ... invariant")
    for num in ids:
        if not re.search(rf"\bS{num}\b", verification):
            fails.append(
                f"S{num} has no row in Verification: name a test, a command and its result, "
                "or `manual:` with what was done and what was seen"
            )
    if verification and "manual:" not in verification.lower():
        has_evidence = re.search(r"[`|]", verification)
        if not has_evidence:
            fails.append("Verification names no test, command or `manual:` observation")

    rejected = lowered.get("rejected", "").strip()
    if rejected and words(rejected) < MIN_SLOT_WORDS:
        fails.append("Rejected is empty: list what was turned down, or `none considered`")
    return fails


# ------------------------------------------------------------------- generated documentation


# Everything between these markers in a documentation file is generated by `rules --markdown`
# and verified by `docs --check`. Nothing else in the file is touched.
BEGIN = "<!-- spec-check:rules -->"
END = "<!-- /spec-check:rules -->"

# A sentence somebody marked as a checkable claim, and a phrasing that has been retired.
CLAIM_RE = re.compile(r"<!--\s*claim:\s*([a-z0-9][a-z0-9-]*)\s*-->")
RETIRED_RE = re.compile(r'<!--\s*retired:\s*"(.+?)"\s*-->')


def rules_markdown() -> str:
    """The rules this file ENFORCES, as markdown, generated from the constants above.

    WHY THIS EXISTS
    ===============

    Documentation that restates a rule is documentation that can rot, and when it rots
    nothing goes red: the code changes, the sentence keeps its confident present tense, and
    the next reader believes whichever copy they found first. On the change that introduced
    the union rule, three such sentences were left standing beside their replacements in two
    files, each one found by a human-scale reviewer and none by any check.

    So the facts a consumer's documentation restates are emitted from here instead. A tier
    table, a slot list and the tier-2 sources are all derived from the same constants the
    enforcement reads, which makes "the documentation disagrees with the tool" a state that
    cannot be reached through this path.

    It deliberately carries no version or sha: a block that embedded one would go stale on
    every commit to this repository, and a check that fires when nothing is wrong is one
    everybody learns to re-run blindly.
    """
    tiers = "\n".join(
        f"| {level} | {', '.join(f'`{s}:`' for s in REQUIRED_SLOTS[level])} | {NEEDS[level]} |"
        for level in (0, 1, 2)
    )
    slots = "\n".join(f"| `{slot}:` | {SLOT_MEANING[slot]} |" for slot in SLOTS)
    defaults = ", ".join(f"`{g}`" for g in DEFAULT_TIER2)
    return f"""\
{BEGIN}
<!-- Generated by `spec-check rules --markdown`. Do not edit between these markers; run
     `spec-check docs --write <this file>` after changing the tool. -->

**Tiers.** The tier is computed from the paths a change touches (`spec-check tier` prints it
and names the source of the list it used).

| Tier | Slots required | Needs |
|---|---|---|
{tiers}

**Slots.** Content is judged by presence of facts, never length or wording: a slot must carry
at least {MIN_SLOT_WORDS} words and must not repeat the subject. A subject is capped at
{MAX_SUBJECT} characters.

| Slot | Holds |
|---|---|
{slots}

`Verified: not run (<reason>)` is accepted, is never reported as verified, and cannot pass a
tier-2 change.

**The tier-2 list is the UNION of two sources**, never a precedence order: `{TIER2_FILE}` in
the repository root, and the `paths:` block of a review workflow (default `{WORKFLOW}`). The
checker runs against the pull request's own checkout, so the branch supplies the
configuration; a union means a file in the branch can only ever make MORE paths tier 2.

Where a repository names neither, these built-in defaults apply: {defaults}.

**A spec file** needs the front-matter keys {", ".join(f"`{k}`" for k in SPEC_KEYS)} and the
sections {", ".join(f"`{s}`" for s in SPEC_SECTIONS)}. Every numbered invariant `Sn` in
Invariants needs a row naming it in Verification: a test, a command with its result, or
`manual:` with what was done and what was seen.

**A spec path outside the repository** is accepted unvalidated only under a prefix the
deployment configures in `SPEC_EXTERNAL_PREFIX`, and fails as a missing file otherwise.

Exit codes: `0` pass, `1` fail with the missing slot named, `2` the tool could not run.
{END}"""


def sync_blocks(text: str) -> tuple[str, int]:
    """Replace every generated block in `text`. Returns the new text and how many it found."""
    block = rules_markdown()
    out: list[str] = []
    count = 0
    rest = text
    while True:
        start = rest.find(BEGIN)
        if start == -1:
            out.append(rest)
            break
        stop = rest.find(END, start)
        if stop == -1:
            raise ToolError(f"{BEGIN} with no closing {END}")
        out.append(rest[:start])
        out.append(block)
        rest = rest[stop + len(END) :]
        count += 1
    return "".join(out), count


def doc_failures(path: Path) -> tuple[list[str], list[str]]:
    """What is wrong with one documentation file, and the claim tags it carries.

    Three things are checked, and the third is the one that catches a sentence left beside
    its replacement:

    1. The file has at least one generated block. A file named on the command line and
       carrying none is a mistake in the gate's configuration, not a pass.
    2. Every generated block matches what this version of the tool emits.
    3. No phrasing marked `<!-- retired: "..." -->` appears anywhere else in the file. That
       is how a claim that has been superseded stays dead: whoever retires it writes the old
       words down once, and the check refuses the file if they come back.
    """
    text = path.read_text(encoding="utf-8")
    fails: list[str] = []
    tags = CLAIM_RE.findall(text)

    try:
        synced, count = sync_blocks(text)
    except ToolError as exc:
        return [str(exc)], tags
    if count == 0:
        fails.append(
            f"no `{BEGIN}` block. Add one where the tier and slot rules are described, or "
            "stop passing this file to `docs --check`"
        )
    elif synced != text:
        fails.append(
            "the generated block is out of date: run `spec-check docs --write "
            f"{path}` and read what changed"
        )

    for phrase in RETIRED_RE.findall(text):
        # The directive line itself holds the phrase, so it is excluded from the search.
        without_directives = RETIRED_RE.sub("", text)
        if phrase in without_directives:
            fails.append(
                f'retired phrasing is back: "{phrase}". It was marked retired in this file, '
                "so either the claim is true again (remove the directive and say why) or a "
                "superseded sentence was left standing"
            )
    return fails, tags


# ---------------------------------------------------------------------------------- commands


def report(fails: list[str], *, warn: bool, label: str) -> int:
    if not fails:
        print(f"spec-check: {label} OK")
        return 0
    head = "spec-check" if not warn else "spec-check (warning)"
    print(f"{head}: {label} is missing the record:", file=sys.stderr)
    for fail in fails:
        print(f"  - {fail}", file=sys.stderr)
    if warn:
        print(
            "\nNot blocking the commit. The pull request gate is not so forgiving.",
            file=sys.stderr,
        )
        return 0
    return 1


def resolve_tier(args: argparse.Namespace) -> tuple[Tier, Path]:
    root = repo_root()
    globs, source = tier2_globs(root, getattr(args, "workflow", WORKFLOW) or WORKFLOW)
    if getattr(args, "files", None):
        files = list(args.files)
    else:
        files = changed_files(root, args.base)
    return tier_for(files, globs, source), root


NEEDS = (
    "a `Spec: none (<reason>)` trailer",
    "Why/Rule/Verified in the message, which is the spec",
    "a spec file, named in a `Spec:` trailer",
)


def cmd_tier(args: argparse.Namespace) -> int:
    tier, root = resolve_tier(args)
    print(tier.explain())
    print(f"needs: {NEEDS[tier.level]}")
    if tier.level < 2:
        # Said whether or not it fired, because "where would tier 2 have come from" is not
        # answerable after the fact, and a repository whose list is the built-in defaults
        # usually wants to write its own.
        _, source = tier2_globs(root, getattr(args, "workflow", WORKFLOW) or WORKFLOW)
        print(f"tier-2 list: {source}")
    return 0


def cmd_msg(args: argparse.Namespace) -> int:
    text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    tier, root = resolve_tier(args)
    fails = check_message(text, tier, root=root)
    return report(fails, warn=args.warn, label="commit message")


def cmd_spec(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if not path.is_file():
        raise ToolError(f"{path} does not exist")
    return report(spec_failures(path), warn=False, label=str(path))


def cmd_rules(args: argparse.Namespace) -> int:
    print(rules_markdown())
    return 0


def cmd_docs(args: argparse.Namespace) -> int:
    """`--check` is the gate; `--write` is how an agent fixes what it reports."""
    if args.check and args.write:
        raise ToolError("--check and --write ask for opposite things")
    rc = 0
    for name in args.files:
        path = Path(name)
        if not path.is_file():
            raise ToolError(f"{path} does not exist")
        if args.write:
            text = path.read_text(encoding="utf-8")
            synced, count = sync_blocks(text)
            if count == 0:
                print(f"spec-check: {path} has no generated block to write", file=sys.stderr)
                rc = 1
                continue
            if synced != text:
                path.write_text(synced, encoding="utf-8")
                print(f"spec-check: rewrote the generated block in {path}")
            else:
                print(f"spec-check: {path} already current")
            continue
        fails, tags = doc_failures(path)
        if args.claims:
            for tag in tags:
                print(tag)
            continue
        if report(fails, warn=False, label=str(path)) != 0:
            rc = 1
    return rc


def cmd_pr(args: argparse.Namespace) -> int:
    text = (
        sys.stdin.read()
        if args.body_file == "-"
        else Path(args.body_file).read_text(encoding="utf-8")
    )
    if args.title and not text.lstrip().startswith(args.title.strip()):
        # A pull request body has no subject line of its own: GitHub keeps the title in its
        # own field. Gluing them back together is what lets one checker read both a commit
        # message and a pull request body.
        text = f"{args.title.strip()}\n\n{text}"
    tier, root = resolve_tier(args)
    fails = check_message(text, tier, root=root, where="pull request body")
    return report(fails, warn=False, label="pull request body")


# ---------------------------------------------------------------------------------- selftest


def selftest() -> int:
    """The cases that prove the rules in docs/specs/2026-11-14-spec-rules.md.

    Kept here as well as in `tests/test_spec_check.py` for one reason: this file is also the
    vault launcher's target on machines with no pytest and no repository checkout of this
    project, and a tool whose rules cannot be checked where it runs is a tool nobody trusts.
    """
    cases: list[tuple[str, bool, str]] = []

    def case(name: str, ok: bool, text: str, tier: int = 1) -> None:
        fails = check_message(text, Tier(tier, ["selftest"]))
        passed = (not fails) == ok
        cases.append((name, passed, "; ".join(fails)))

    good = (
        "feat(ui): the map is the link, text link goes\n\n"
        "Why: answer carried a picture and a blue link to the same place.\n"
        "Rule: image src is never model-supplied; url goes through _url allowlist.\n"
        "Verified: 390x844, hostile url drew unlinked, 0 off-origin images.\n"
        "Spec: in-message\n"
        "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>\n"
    )
    case("caveman body with every slot", True, good)
    case(
        "verbose body with the same facts",
        True,
        "feat(ui): make the map itself the link\n\n"
        "Why: the directions answer was carrying a picture and then a line of blue text\n"
        "underneath it pointing at exactly the same place.\n"
        "Rule: the image src is never supplied by the model, and the optional url is put\n"
        "through the same _url allowlist that the link block already uses.\n"
        "Verified: checked at 390x844 in the browser, the hostile url drew without a link\n"
        "and there were zero images pointing off this origin.\n"
        "Spec: in-message\n",
    )
    case("no Why", False, good.replace("Why: answer carried a picture and a blue link to the same place.\n", ""))
    case("no Verified", False, good.replace("Verified: 390x844, hostile url drew unlinked, 0 off-origin images.\n", ""))
    case("no Spec trailer", False, good.replace("Spec: in-message\n", ""))
    case("Spec none without a reason", False, good.replace("Spec: in-message", "Spec: none"))
    case("Spec none with a reason on tier 0", True, "docs: fix a typo\n\nSpec: none (prompt text)\n", 0)
    case("Spec none with a reason on tier 2", False, good.replace("Spec: in-message", "Spec: none (felt small)"), 2)
    case("tier 2 wants a spec file", False, good, 2)
    case(
        "filler with no facts",
        False,
        "fix: things\n\nWhy: stuff\nRule: ok\nVerified: yes\nSpec: in-message\n",
    )
    case("slot repeating the subject", False,
         "fix(cache): drop the stale weather entry\n\n"
         "Why: fix(cache): drop the stale weather entry\n"
         "Rule: the cache entry without hourly is treated as stale and refetched.\n"
         "Verified: pytest tests/test_cache.py -q, 14 passed.\n"
         "Spec: in-message\n")
    case("not run without a reason", False, good.replace(
        "Verified: 390x844, hostile url drew unlinked, 0 off-origin images.",
        "Verified: not run"))
    case("not run with a reason on tier 1", True, good.replace(
        "Verified: 390x844, hostile url drew unlinked, 0 off-origin images.",
        "Verified: not run (no hardware here; it is a comment change)"))
    case("merge commit is not prose", True, "Merge pull request #34 from lacrx/feat/x\n\nwhatever\n", 2)
    case("fixup is not prose", True, "fixup! feat(ui): map is the link\n", 2)
    case("subject over the cap", False, "feat(ui): " + "x" * 80 + "\n\n" + good.split("\n", 1)[1])

    # Tier inference, against a fixed list rather than whatever repository this is standing
    # in, so the selftest answers the same thing everywhere.
    globs = ["app/config.py", ".github/workflows/**"]
    checks = (
        ("a listed path is tier 2", ["app/config.py"], 2),
        ("a glob with ** is tier 2", [".github/workflows/ci.yml"], 2),
        ("ordinary code is tier 1", ["app/servers/list_server.py"], 1),
        ("prose is tier 0", ["docs/architecture.md"], 0),
        ("one listed path in a mixed change wins", ["README.md", "app/config.py"], 2),
    )
    for name, files, want in checks:
        got = tier_for(files, globs, "selftest")
        cases.append((name, got.level == want, got.explain()))

    # The built-in fallback, which is the thing that was documented and missing.
    for name, files, want in (
        ("a workflow is tier 2 by default", [".github/workflows/deploy.yml"], 2),
        ("a Dockerfile is tier 2 by default", ["Dockerfile"], 2),
        ("an ordinary module is not", ["src/widgets.py"], 1),
    ):
        got = tier_for(files, list(DEFAULT_TIER2), "built-in defaults")
        cases.append((name, got.level == want, got.explain()))

    bad = 0
    for name, ok, detail in cases:
        print(f"{'ok  ' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if not ok else ""))
        bad += 0 if ok else 1
    print(f"\n{len(cases) - bad}/{len(cases)} passed")
    return 1 if bad else 0


# -------------------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--selftest", action="store_true", help="run the rule cases and exit")
    sub = parser.add_subparsers(dest="cmd")

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--base", default=os.environ.get("SPEC_BASE", "origin/main"))
        p.add_argument("--files", nargs="*", help="treat these as the changed files")
        p.add_argument(
            "--workflow",
            default=os.environ.get("SPEC_WORKFLOW", WORKFLOW),
            help="the review workflow whose `paths:` block is the tier-2 list",
        )

    p_tier = sub.add_parser("tier", help="what tier this change is, and why")
    add_common(p_tier)
    p_tier.set_defaults(func=cmd_tier)

    p_msg = sub.add_parser("msg", help="check a commit message")
    p_msg.add_argument("file")
    p_msg.add_argument("--warn", action="store_true", help="report, do not fail")
    add_common(p_msg)
    p_msg.set_defaults(func=cmd_msg)

    p_spec = sub.add_parser("spec", help="check a spec file")
    p_spec.add_argument("path")
    p_spec.set_defaults(func=cmd_spec)

    p_rules = sub.add_parser("rules", help="print the rules this tool enforces")
    p_rules.add_argument("--markdown", action="store_true", help="the only format there is")
    p_rules.set_defaults(func=cmd_rules)

    p_docs = sub.add_parser("docs", help="check or rewrite generated blocks in documentation")
    p_docs.add_argument("files", nargs="+")
    # **`--check` is accepted explicitly, and that is not sugar.** Checking is the default,
    # so the first version had no such flag while the README, the action and this
    # repository's own CI all called `docs --check <file>`: three documents describing a flag
    # the code did not have, caught by that CI on the first run. The failure mode of the
    # feature being built, inside the feature being built. Callers say what they mean.
    p_docs.add_argument("--check", action="store_true", help="verify the blocks (the default)")
    p_docs.add_argument("--write", action="store_true", help="rewrite the blocks in place")
    p_docs.add_argument("--claims", action="store_true", help="print the claim tags instead")
    p_docs.set_defaults(func=cmd_docs)

    p_pr = sub.add_parser("pr", help="check a pull request body")
    p_pr.add_argument("--body-file", required=True)
    p_pr.add_argument("--title", default="")
    add_common(p_pr)
    p_pr.set_defaults(func=cmd_pr)

    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        return args.func(args)
    except ToolError as exc:
        print(f"spec-check: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
