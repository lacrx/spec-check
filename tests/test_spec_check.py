"""`scripts/spec_check.py` has to fail for the right reason and pass for the right reason.

The gate it enforces is only worth having if it never fires when nothing is wrong. CLAUDE.md
records what happens otherwise twice: a green tick that meant "the job ran", and five
pre-existing ruff errors that made `premerge.sh` unreadable on a clean `main`. A check you
learn to bypass is worse than no check.

So the cases here are mostly about the boundary between a message that carries facts and one
that carries words. `test_terse_and_verbose_bodies_with_the_same_facts_both_pass` is the one
that defines the whole design: the gate counts facts and never style, which is what lets
commit prose be caveman-dense without losing the record.

The invariant ids in the docstrings are the ones in `examples/SPEC-TEMPLATE.md`.
"""

import importlib.util
import re
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "spec_check.py"

_spec = importlib.util.spec_from_file_location("spec_check", SCRIPT)
sc = importlib.util.module_from_spec(_spec)
# Registered BEFORE it is executed: `@dataclass` resolves annotations through
# `sys.modules[cls.__module__]`, so a module loaded by path and not registered raises
# AttributeError on the first dataclass in the file rather than on anything to do with the
# tests.
sys.modules["spec_check"] = sc
_spec.loader.exec_module(sc)

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "examples" / "SPEC-TEMPLATE.md"

GOOD = (
    "feat(ui): the map is the link, text link goes\n"
    "\n"
    "Why: answer carried a picture and a blue link to the same place.\n"
    "Rule: image src is never model-supplied; url goes through the _url allowlist.\n"
    "Verified: 390x844, hostile url drew unlinked, 0 off-origin images.\n"
    "Spec: in-message\n"
    "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>\n"
)


def fails(text, tier=1, root=None):
    return sc.check_message(text, sc.Tier(tier, ["test"]), root=root)


class TestMessage:
    """S4, S5, S6, S7."""

    def test_a_complete_terse_body_passes(self):
        assert fails(GOOD) == []

    def test_terse_and_verbose_bodies_with_the_same_facts_both_pass(self):
        verbose = (
            "feat(ui): make the map itself the link\n"
            "\n"
            "Why: the directions answer was carrying a picture and then a line of blue\n"
            "text underneath it that pointed at exactly the same place, which is two\n"
            "components for one destination.\n"
            "Rule: the image src is never supplied by the model, because an <img> is\n"
            "fetched the instant the answer paints. The optional url is an href and needs\n"
            "a tap, so it goes through the same _url allowlist the link block uses.\n"
            "Verified: checked at 390x844 in the browser. The hostile url drew without a\n"
            "link and there were zero images pointing off this origin.\n"
            "Spec: in-message\n"
        )
        assert fails(verbose) == []
        assert fails(GOOD) == []

    @pytest.mark.parametrize("slot", ["Why", "Rule", "Verified"])
    def test_a_missing_slot_fails_and_names_itself(self, slot):
        stripped = "\n".join(
            line for line in GOOD.split("\n") if not line.startswith(f"{slot}:")
        )
        found = fails(stripped)
        assert found, f"{slot} removed and nothing complained"
        assert any(f"`{slot}:`" in f for f in found), found

    def test_a_body_of_filler_fails(self):
        thin = (
            "fix: things\n\nWhy: stuff\nRule: ok\nVerified: yes\nSpec: in-message\n"
        )
        found = fails(thin)
        assert len(found) == 3, found

    def test_slot_repeating_the_subject_fails(self):
        echo = GOOD.replace(
            "Why: answer carried a picture and a blue link to the same place.",
            "Why: feat(ui): the map is the link, text link goes",
        )
        assert any("repeats the subject" in f for f in fails(echo))

    def test_no_spec_trailer_fails(self):
        assert any("`Spec:` trailer" in f for f in fails(GOOD.replace("Spec: in-message\n", "")))

    def test_spec_none_passes_tier_zero_and_fails_tier_two(self):
        body = "docs: fix a typo in the directions note\n\nSpec: none (prompt text only)\n"
        assert fails(body, tier=0) == []
        assert any("needs a spec file" in f for f in fails(body, tier=2))

    def test_spec_none_without_a_reason_fails(self):
        assert any("needs a reason" in f for f in fails(GOOD.replace("Spec: in-message", "Spec: none")))

    def test_not_run_needs_a_reason(self):
        bare = GOOD.replace(
            "Verified: 390x844, hostile url drew unlinked, 0 off-origin images.",
            "Verified: not run",
        )
        assert any("needs a reason" in f for f in fails(bare))

    def test_not_run_with_a_reason_passes_below_the_boundary(self):
        excused = GOOD.replace(
            "Verified: 390x844, hostile url drew unlinked, 0 off-origin images.",
            "Verified: not run (comment change, no hardware here)",
        )
        assert fails(excused, tier=1) == []

    def test_tier_two_cannot_ship_unverified(self):
        excused = GOOD.replace(
            "Verified: 390x844, hostile url drew unlinked, 0 off-origin images.",
            "Verified: not run (comment change, no hardware here)",
        ).replace("Spec: in-message", "Spec: examples/SPEC-TEMPLATE.md")
        found = fails(excused, tier=2, root=ROOT)
        assert any("cannot ship unverified" in f for f in found), found

    def test_subject_over_the_cap_fails(self):
        long = "feat(geoff): " + "x" * 80 + GOOD[GOOD.index("\n") :]
        assert any("cap is 72" in f for f in fails(long))

    def test_a_subject_ending_in_a_period_fails(self):
        assert any("period" in f for f in fails(GOOD.replace("goes\n", "goes.\n", 1)))

    @pytest.mark.parametrize(
        "subject",
        [
            "Merge pull request #34 from acme/feat/tappable-map",
            # GitHub's synthetic merge commit, which is HEAD in a `pull_request` checkout.
            "Merge d1d0580233205446bfe02977bec1982954238189 into e1016f1289f128caabee31d2f4cd6ffa7f04e6de",
            "Merge branch 'main' into feat/x",
            "fixup! feat(ui): the map is the link",
            "WIP: still thinking",
            "Revert \"feat(ui): the map is the link\"",
        ],
    )
    def test_generated_and_in_flight_subjects_are_left_alone(self, subject):
        assert fails(f"{subject}\n\nwhatever the tooling wrote\n", tier=2) == []

    @pytest.mark.parametrize(
        "subject",
        [
            "Wipe stale thumbnails on startup",
            "wiping the idempotency ledger on boot",
            "Document why merge branch fails here",
            # Close to the synthetic form and still authored prose: no second sha.
            "Merge deadbeef1234567 handling into the resolver",
            "chore: revert to the documented Apple URL form",
        ],
    )
    def test_an_ordinary_subject_is_never_mistaken_for_a_generated_one(self, subject):
        """Found by the reviewer on this file's own pull request.

        `wip` was in a `startswith` tuple with no word boundary, so `Wipe stale mapshots on
        startup` skipped the entire check: an empty body passed at tier 2, and auto-merge
        would have read that green tick as the record being there. `SKIP_SUBJECTS` was
        matched anywhere in the line, which caught any subject mentioning a merge.
        """
        assert sc.parse_message(f"{subject}\n\nnothing else\n").skip is False
        assert fails(f"{subject}\n\nnothing else\n", tier=2) != []

    def test_spec_trailer_ends_with_its_own_line(self):
        """PR #37, this change's own first pull request, and the reason the gate was red.

        `Spec:` was a multi-line slot like the others, so the blank line and the "Generated
        with Claude Code" attribution after it were read as part of the path. The gate then
        said `examples/SPEC-TEMPLATE.md\\n\\n(attribution)` did not exist in the
        repository, which was true of the string and false of the path, and pointed at the
        wrong problem.
        """
        body = (
            "feat(ci): a spec gate, and prose that keeps the record\n"
            "\n"
            "Why: the record of a change did not carry the fact that would catch it.\n"
            "Rule: named slots a checker counts, and a tier read from the review list.\n"
            "Verified: 1117 passed, 2 skipped in the image; ruff clean.\n"
            "\n"
            "Spec: examples/SPEC-TEMPLATE.md\n"
            "\n"
            "Generated with Claude Code\n"
        )
        assert sc.parse_message(body).slots["Spec"] == "examples/SPEC-TEMPLATE.md"
        assert fails(body, tier=2, root=ROOT) == []

    def test_a_table_under_rule_belongs_to_it(self):
        body = (
            "feat(review): tier a change by what it touches\n"
            "\n"
            "Why: every change was treated the same whatever it could break.\n"
            "Rule: the tier is computed from the paths touched:\n"
            "\n"
            "| Tier | Needs |\n"
            "|---|---|\n"
            "| 2 | a spec file |\n"
            "\n"
            "Verified: selftest 19/19, tier inference against the real workflow.\n"
            "Spec: in-message\n"
        )
        assert "| 2 | a spec file |" in sc.parse_message(body).slots["Rule"]
        assert fails(body) == []

    def test_a_new_paragraph_ends_a_slot(self):
        body = (
            "fix(cache): drop the stale weather entry entry\n"
            "\n"
            "Why: an entry without hourly data was served to the panel.\n"
            "Rule: an entry with no hourly block is stale and refetched.\n"
            "Verified: pytest tests/test_cache.py -q, 14 passed.\n"
            "\n"
            "This closing paragraph is prose about the change and belongs to no slot.\n"
            "\n"
            "Spec: in-message\n"
        )
        slots = sc.parse_message(body).slots
        assert "closing paragraph" not in slots["Verified"]
        assert fails(body) == []

    def test_a_fenced_block_does_not_eat_the_trailers(self):
        """A pull request body quoting a message must not have its own slots read out of the
        quote. The fence is skipped, so the real `Spec:` line is still the one that counts."""
        quoting = (
            "feat(ui): the map is the link\n"
            "\n"
            "Why: a picture and a link said the same thing.\n"
            "Rule: the model never supplies the image src.\n"
            "Verified: 390x844 in the browser, zero off-origin images.\n"
            "\n"
            "```\n"
            "Spec: none (this is an example inside a fence)\n"
            "```\n"
            "\n"
            "Spec: in-message\n"
        )
        assert fails(quoting) == []


WORKFLOW_FIXTURE = """\
name: Code Review
on:
  pull_request:
    paths:
      # A comment inside the block, which is most of the real file.
      - 'app/config.py'
      - 'app/auth/**'
      - '.github/workflows/**'
jobs:
  review:
    runs-on: ubuntu-latest
"""


class TestTier:
    """S1, and the precedence between the three places a tier-2 list can come from."""

    globs = ["app/config.py", "app/auth/**", ".github/workflows/**"]

    @pytest.mark.parametrize(
        "path", ["app/config.py", "app/auth/session.py", ".github/workflows/x.yml"]
    )
    def test_listed_paths_are_tier_two(self, path):
        assert sc.tier_for([path], self.globs).level == 2

    @pytest.mark.parametrize("path", ["docs/architecture.md", "README.md", "app/prompts/x.txt"])
    def test_prose_is_tier_zero(self, path):
        assert sc.tier_for([path], self.globs).level == 0

    @pytest.mark.parametrize("path", ["app/servers/list_server.py", "scripts/envset.py"])
    def test_ordinary_code_is_tier_one(self, path):
        assert sc.tier_for([path], self.globs).level == 1

    def test_one_boundary_file_in_a_big_change_still_wins(self):
        tier = sc.tier_for(["README.md", "app/prompts/x.txt", "app/config.py"], self.globs)
        assert tier.level == 2
        assert "app/config.py" in tier.explain()

    def test_no_files_is_tier_zero(self):
        assert sc.tier_for([], self.globs).level == 0

    def test_a_paths_block_is_parsed_out_of_a_workflow(self, tmp_path):
        wf = tmp_path / ".github" / "workflows" / "code-review.yml"
        wf.parent.mkdir(parents=True)
        wf.write_text(WORKFLOW_FIXTURE)
        assert sc.boundary_globs(tmp_path) == self.globs

    def test_the_comment_in_the_block_is_skipped_not_parsed(self, tmp_path):
        wf = tmp_path / ".github" / "workflows" / "code-review.yml"
        wf.parent.mkdir(parents=True)
        wf.write_text(WORKFLOW_FIXTURE)
        assert not any("comment" in g for g in sc.boundary_globs(tmp_path))

    @pytest.mark.parametrize(
        "path", [".github/workflows/deploy.yml", "Dockerfile", "deploy/run.sh", "src/app/config.py"]
    )
    def test_the_built_in_list_covers_a_repository_with_nothing_of_its_own(self, tmp_path, path):
        """The fallback was documented before it existed.

        The first version of this tool promised a built-in list for repositories with no
        review workflow and returned an empty one, so every change in such a repository was
        tier 1 at most. It was invisible where the tool was written, because that repository
        has a workflow, and would have been silently wrong everywhere else.
        """
        globs, source = sc.tier2_globs(tmp_path)
        assert source == "built-in defaults"
        assert sc.tier_for([path], globs, source).level == 2

    def test_a_repositorys_own_file_adds_to_the_workflow_and_never_replaces_it(self, tmp_path):
        """The branch supplies the configuration, so a repository file must only ADD.

        Found by a reviewer on the pull request that introduced the file: under a precedence
        order, a branch could edit a sensitive path and add a `.spec-tier2` naming one
        unrelated glob. Its own file would win, the change would grade tier 1, no spec would
        be required, and a clean review would merge it. The thing being gated chose the
        gate's settings in the same change, and nothing went red.
        """
        wf = tmp_path / ".github" / "workflows" / "code-review.yml"
        wf.parent.mkdir(parents=True)
        wf.write_text(WORKFLOW_FIXTURE)
        (tmp_path / ".spec-tier2").write_text(
            "# what this repository calls its boundary\nscripts/pay*.py\n\nservices/billing/**\n"
        )
        globs, source = sc.tier2_globs(tmp_path)
        assert source == ".spec-tier2 + .github/workflows/code-review.yml paths:"
        assert globs == ["scripts/pay*.py", "services/billing/**", *self.globs]
        assert sc.tier_for(["scripts/pay_out.py"], globs, source).level == 2
        # The workflow's list still applies in full. This is the assertion that would have
        # caught the hole.
        assert sc.tier_for(["app/config.py"], globs, source).level == 2
        assert sc.tier_for(["app/auth/session.py"], globs, source).level == 2

    def test_a_branch_cannot_demote_itself_by_adding_a_file(self, tmp_path):
        wf = tmp_path / ".github" / "workflows" / "code-review.yml"
        wf.parent.mkdir(parents=True)
        wf.write_text(WORKFLOW_FIXTURE)
        before, src_before = sc.tier2_globs(tmp_path)
        assert sc.tier_for(["app/config.py"], before, src_before).level == 2
        (tmp_path / ".spec-tier2").write_text("docs/nothing-important/**\n")
        after, src_after = sc.tier2_globs(tmp_path)
        assert set(before) <= set(after), "a repository file narrowed the list"
        assert sc.tier_for(["app/config.py"], after, src_after).level == 2

    def test_duplicates_across_the_two_sources_collapse(self, tmp_path):
        wf = tmp_path / ".github" / "workflows" / "code-review.yml"
        wf.parent.mkdir(parents=True)
        wf.write_text(WORKFLOW_FIXTURE)
        (tmp_path / ".spec-tier2").write_text("app/config.py\nextra/**\n")
        globs, _ = sc.tier2_globs(tmp_path)
        assert globs.count("app/config.py") == 1

    def test_the_workflow_replaces_the_defaults(self, tmp_path):
        wf = tmp_path / ".github" / "workflows" / "code-review.yml"
        wf.parent.mkdir(parents=True)
        wf.write_text(WORKFLOW_FIXTURE)
        globs, source = sc.tier2_globs(tmp_path)
        assert source.endswith("paths:")
        assert globs == self.globs
        # A Dockerfile is in the built-in list and not in this repository's, so a repository
        # that states its own list owns the answer completely.
        assert sc.tier_for(["Dockerfile"], globs, source).level == 1

    def test_the_source_is_named_in_the_explanation(self, tmp_path):
        globs, source = sc.tier2_globs(tmp_path)
        assert "built-in defaults" in sc.tier_for(["Dockerfile"], globs, source).explain()


class TestSpecFile:
    """S2, S3."""

    def good(self):
        return (TEMPLATE).read_text()

    def test_the_template_passes(self):
        assert sc.spec_failures(TEMPLATE) == []

    @pytest.mark.parametrize("key", ["id", "tier", "status", "quiet", "paths"])
    def test_a_missing_front_matter_key_fails(self, key, tmp_path):
        text = "\n".join(
            line for line in self.good().split("\n") if not line.startswith(f"{key}:")
        )
        if key == "paths":
            text = "\n".join(line for line in text.split("\n") if not line.startswith("  - "))
        path = tmp_path / "s.md"
        path.write_text(text)
        assert any(f"`{key}`" in f for f in sc.spec_failures(path)), sc.spec_failures(path)

    @pytest.mark.parametrize("section", ["Problem", "Invariants", "Blast radius", "Verification", "Rejected"])
    def test_a_missing_section_fails(self, section, tmp_path):
        text = self.good().replace(f"## {section}", f"## {section} (renamed away)")
        path = tmp_path / "s.md"
        path.write_text(text)
        assert any(f"## {section}" in f for f in sc.spec_failures(path))

    def test_invariant_without_a_verification_row_fails(self, tmp_path):
        text = self.good()
        head, _, rest = text.partition("## Verification")
        table, _, tail = rest.partition("## Rejected")
        table = "\n".join(line for line in table.split("\n") if "S3" not in line)
        path = tmp_path / "s.md"
        path.write_text(f"{head}## Verification{table}## Rejected{tail}")
        found = sc.spec_failures(path)
        assert any("S3 has no row" in f for f in found), found

    def test_a_bad_status_fails(self, tmp_path):
        path = tmp_path / "s.md"
        path.write_text(self.good().replace("status: proposed", "status: nearly"))
        assert any("not one of" in f for f in sc.spec_failures(path))

    def test_no_front_matter_fails(self, tmp_path):
        path = tmp_path / "s.md"
        path.write_text("# a spec with no header\n\n## Problem\n\nthings\n")
        assert any("no front matter" in f for f in sc.spec_failures(path))

    def test_a_spec_with_no_numbered_invariant_fails(self, tmp_path):
        text = self.good()
        head, _, rest = text.partition("## Invariants")
        _, _, tail = rest.partition("## Blast radius")
        path = tmp_path / "s.md"
        path.write_text(f"{head}## Invariants\n\nit should work nicely.\n\n## Blast radius{tail}")
        assert any("no numbered" in f for f in sc.spec_failures(path))


class TestTheToolItself:
    def test_a_missing_spec_reference_fails_rather_than_crashing(self):
        body = GOOD.replace("Spec: in-message", "Spec: docs/specs/not-a-file.md")
        assert any("does not exist" in f for f in fails(body, tier=2, root=ROOT))

    def test_a_backticked_spec_path_is_still_that_path(self):
        """PR #38 named its spec in backticks and the gate said the file did not exist.

        A pull request body is rendered markdown and this gate's own message tells you to
        name a path in it, so backticks around that path are what anybody writes. The file
        was in the branch the whole time.
        """
        body = GOOD.replace("Spec: in-message", "Spec: `examples/SPEC-TEMPLATE.md`")
        assert fails(body, tier=2, root=ROOT) == []

    def test_a_backticked_path_that_is_really_missing_still_fails_and_says_which(self):
        """Stripping the quoting must not stop the check, and the message names the path.

        The whole cost of the original bug was a message that quoted the path back with its
        backticks still on, which reads like the path being wrong rather than the parser.
        """
        body = GOOD.replace("Spec: in-message", "Spec: `docs/specs/not-a-file.md`")
        found = fails(body, tier=2, root=ROOT)
        assert any("`Spec: docs/specs/not-a-file.md` does not exist" in f for f in found), found

    def test_a_spec_outside_the_repository_needs_the_prefix_configured(self, monkeypatch):
        """Some changes have no repository: machine state, a rule spanning several repos.

        Their specs live wherever that is, which the runner usually cannot read. Accepting
        such a path silently for everyone would be a hole; refusing it for everyone would
        make those changes unrecordable. So it is one environment variable, set by the
        deployment that keeps specs elsewhere, and empty by default.
        """
        body = GOOD.replace("Spec: in-message", "Spec: notes/specs/x.md")
        monkeypatch.delenv("SPEC_EXTERNAL_PREFIX", raising=False)
        assert any("does not exist" in f for f in fails(body, tier=2, root=ROOT))
        monkeypatch.setenv("SPEC_EXTERNAL_PREFIX", "notes/,vault/")
        assert fails(body, tier=2, root=ROOT) == []

    def test_selftest_passes(self):
        assert sc.selftest() == 0

    def test_a_broken_spec_path_is_a_tool_error_not_a_verdict(self):
        assert sc.main(["spec", "/nonexistent/spec.md"]) == 2


class TestGeneratedDocs:
    """The rules a consumer's documentation restates are emitted, not retyped.

    This class exists because of what the reviewer found on the pull request that introduced
    the union rule: three sentences in two files were left standing beside their
    replacements, each one asserting the precedence order the code no longer had. Every one
    was in the confident present tense, and nothing anywhere went red. A reader caught all
    three.

    So the facts the code owns are generated into those files, and `docs --check` fails when
    a block has drifted. `retired:` covers the other half: a phrasing that has been
    superseded is written down once, and the file is refused if it comes back.
    """

    def doc(self, extra: str = "") -> str:
        return f"# A consumer's documentation\n\nNarrative that stays hand written.\n\n{sc.rules_markdown()}\n\n{extra}"

    def test_a_current_block_passes(self, tmp_path):
        path = tmp_path / "CLAUDE.md"
        path.write_text(self.doc())
        fails, _ = sc.doc_failures(path)
        assert fails == []

    def test_an_edited_block_fails(self, tmp_path):
        path = tmp_path / "CLAUDE.md"
        path.write_text(self.doc().replace("the UNION of two sources", "whichever answers first"))
        fails, _ = sc.doc_failures(path)
        assert any("out of date" in f for f in fails), fails

    def test_a_block_goes_stale_when_the_rules_change(self, tmp_path, monkeypatch):
        """The inversion that matters: a change in the tool makes the documentation red.

        Today's failure is the other way round, which is why it is silent: the code changes
        and the prose keeps its tense.
        """
        path = tmp_path / "CLAUDE.md"
        path.write_text(self.doc())
        assert sc.doc_failures(path)[0] == []
        monkeypatch.setitem(sc.REQUIRED_SLOTS, 1, ("Why", "Rule", "Verified", "Cost", "Spec"))
        fails, _ = sc.doc_failures(path)
        assert any("out of date" in f for f in fails), fails

    def test_write_fixes_what_check_reports(self, tmp_path):
        path = tmp_path / "CLAUDE.md"
        path.write_text(self.doc().replace("| Tier | Slots required | Needs |", "| Tier | Needs |"))
        assert sc.doc_failures(path)[0] != []
        assert sc.main(["docs", str(path), "--write"]) == 0
        assert sc.doc_failures(path)[0] == []

    def test_a_file_with_no_block_fails_rather_than_passing(self, tmp_path):
        path = tmp_path / "CLAUDE.md"
        path.write_text("# No block here\n\nJust prose.\n")
        fails, _ = sc.doc_failures(path)
        assert any("no `<!-- spec-check:rules -->` block" in f for f in fails), fails

    def test_an_unclosed_block_is_a_failure_not_a_silent_pass(self, tmp_path):
        path = tmp_path / "CLAUDE.md"
        path.write_text(f"# x\n\n{sc.BEGIN}\nhalf a block\n")
        fails, _ = sc.doc_failures(path)
        assert any("no closing" in f for f in fails), fails

    def test_retired_phrasing_coming_back_fails(self, tmp_path):
        path = tmp_path / "CLAUDE.md"
        path.write_text(
            self.doc(
                '<!-- retired: "wins over both" -->\n\n'
                "A `.spec-tier2` file is unioned with `paths:`, which wins over both.\n"
            )
        )
        fails, _ = sc.doc_failures(path)
        assert any("retired phrasing is back" in f for f in fails), fails

    @pytest.mark.parametrize(
        "comeback",
        [
            # Reflowed across a line break, which is how a hard-wrapped file reproduces a
            # sentence, and the case the raw substring test missed on the very pull request
            # that added the directive.
            "The canonical copy is here, not\nin the vault, which was true for a day.",
            # Capitalised for its new position.
            "the canonical copy is here, not in the vault",
            # Extra spacing from a reflow.
            "The  canonical copy is here,  not in the vault",
            # Inside a list item, indented.
            "  - The canonical copy is here, not in the vault\n",
        ],
    )
    def test_retired_phrasing_is_caught_however_it_is_wrapped(self, tmp_path, comeback):
        path = tmp_path / "CLAUDE.md"
        path.write_text(
            self.doc(
                '<!-- retired: "The canonical copy is here, not in the vault" -->\n\n'
                + comeback
                + "\n"
            )
        )
        fails, _ = sc.doc_failures(path)
        assert any("retired phrasing is back" in f for f in fails), fails

    def test_a_paraphrase_of_a_retired_phrase_is_allowed(self, tmp_path):
        """The record of what was retired has to stay writable, or the directive bans the
        history as well as the claim. A paraphrase is how a file keeps both."""
        path = tmp_path / "CLAUDE.md"
        path.write_text(
            self.doc(
                '<!-- retired: "The canonical copy is here, not in the vault" -->\n\n'
                "It used to say the canonical copy lived in this repository, which stopped\n"
                "being true the day the tool moved.\n"
            )
        )
        fails, _ = sc.doc_failures(path)
        assert fails == [], fails

    def test_a_retired_directive_alone_is_not_a_failure(self, tmp_path):
        path = tmp_path / "CLAUDE.md"
        path.write_text(self.doc('<!-- retired: "wins over both" -->\n\nThe union is the rule.\n'))
        fails, _ = sc.doc_failures(path)
        assert fails == [], fails

    def test_claim_tags_are_reported_for_the_consumer_to_assert(self, tmp_path):
        path = tmp_path / "CLAUDE.md"
        path.write_text(
            self.doc(
                "<!-- claim: canonical-copy-is-the-action -->\n\nThe checker is a pinned action.\n"
                "<!-- claim: no-local-copy -->\n\nThere is no copy in this repository.\n"
            )
        )
        fails, tags = sc.doc_failures(path)
        assert fails == []
        assert tags == ["canonical-copy-is-the-action", "no-local-copy"]

    def test_the_block_names_every_slot_and_tier(self):
        """A generated block that quietly stopped mentioning a rule would be worse than a
        hand-written one, because nobody re-reads generated text."""
        block = sc.rules_markdown()
        for slot in sc.SLOTS:
            assert f"`{slot}:`" in block
        for level in (0, 1, 2):
            assert f"| {level} |" in block
        assert str(sc.MIN_SLOT_WORDS) in block
        assert str(sc.MAX_SUBJECT) in block
        for glob in sc.DEFAULT_TIER2:
            assert f"`{glob}`" in block

    def test_check_is_the_default_and_the_explicit_flag_works(self, tmp_path):
        """Both forms, because every document about this tool used the flag form.

        `--check` did not exist when the README, the action and this repository's CI were all
        written to call it: documentation describing a flag the code did not have, which is
        the failure this whole feature is against, inside this feature. Its own CI caught it
        on the first run with `error: unrecognized arguments: --check`.
        """
        path = tmp_path / "CLAUDE.md"
        path.write_text(self.doc())
        assert sc.main(["docs", str(path)]) == 0
        assert sc.main(["docs", str(path), "--check"]) == 0
        path.write_text(self.doc().replace("the UNION of two sources", "whichever answers"))
        assert sc.main(["docs", str(path)]) == 1
        assert sc.main(["docs", str(path), "--check"]) == 1

    def test_check_and_write_together_is_a_tool_error(self, tmp_path):
        path = tmp_path / "CLAUDE.md"
        path.write_text(self.doc())
        assert sc.main(["docs", str(path), "--check", "--write"]) == 2

    def test_the_block_carries_no_version_or_sha(self):
        """A block embedding the tool's own sha would go stale on every commit here, and a
        check that fires when nothing is wrong is one everybody learns to re-run blindly."""
        block = sc.rules_markdown()
        assert not re.search(r"\b[0-9a-f]{7,40}\b", block)
