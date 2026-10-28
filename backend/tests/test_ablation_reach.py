"""Tests that each ablation switch actually removes the mechanism it names.

`app/hybrid/ablations` states the principle these tests enforce:

    An ablation is only informative if it removes a mechanism *everywhere* it acts.

A controlled ablation of the verification gate violated that principle and went
undetected: both arms produced byte-identical output on all 255 test questions, because
`no_verification` gated only the release decision in `controller.py` and not the
realizer's separate check that the model's phrasing introduced no unsupported figure.
The experiment reported no effect where it had simply failed to remove a guard.

These tests exist so that failure mode is caught by the suite rather than by reading a
table of identical numbers. They assert both directions: the guard is ON by default, and
OFF under the switch. A test that only checked the default would have passed throughout
the period the bug existed.
"""
from __future__ import annotations

import pytest

from app.hybrid import ablations
from app.neural.response_generation import Conclusion, LanguageModelRealizer

pytestmark = pytest.mark.no_db


class _Stub:
    """A language model that returns whatever wording the test wants to release."""

    name = "stub"

    def __init__(self, reply: str) -> None:
        self.reply = reply

    def complete(self, request):
        class _Completion:
            text = self.reply
        return _Completion()


@pytest.fixture
def conclusion():
    """A real Conclusion whose only supported figure is 450."""
    return Conclusion(kind="calculation", subject="your order",
                      headline="the refund is 450 rupees",
                      value="450 rupees")


def _realize(reply: str, conclusion, switches=frozenset()):
    realizer = LanguageModelRealizer.__new__(LanguageModelRealizer)
    realizer.llm = _Stub(reply)
    with ablations.applied(switches):
        return realizer.realize(conclusion)


# --------------------------------------------------------------------------
# the defect this file was written for
# --------------------------------------------------------------------------

def test_invented_figure_is_replaced_by_default(conclusion, monkeypatch):
    """With no ablation, wording that introduces an unsupported figure is rejected."""
    text, from_model = _realize("You will be refunded 900 rupees.", conclusion)
    assert from_model is False
    assert "900" not in text


def test_invented_figure_is_released_under_no_verification(conclusion):
    """Under the switch the same wording must survive, or the ablation measures nothing.

    This is the assertion that would have failed while the bug was present, and the
    reason the controlled gate ablation produced byte-identical arms.
    """
    text, from_model = _realize("You will be refunded 900 rupees.", conclusion,
                                frozenset({"no_verification"}))
    assert from_model is True
    assert "900" in text


def test_supported_figure_is_released_either_way(conclusion):
    """A faithful rewrite is released whether or not the switch is set."""
    for switches in (frozenset(), frozenset({"no_verification"})):
        text, from_model = _realize("Your refund comes to 450 rupees.", conclusion,
                                    switches)
        assert from_model is True and "450" in text


def test_empty_completion_falls_back_even_under_the_ablation(conclusion):
    """Robustness is not verification.

    An empty reply is a provider failure. Falling back must not be ablatable, or the
    ablation arm returns nothing at all and the comparison is meaningless for a reason
    that has nothing to do with verification.
    """
    text, from_model = _realize("", conclusion, frozenset({"no_verification"}))
    assert from_model is False and text


def test_unrelated_switches_do_not_disable_the_figure_check(conclusion):
    """Only `no_verification` may remove this guard.

    If another switch disabled it as a side effect, every ablation row would silently
    measure the figure check as well as its own mechanism.
    """
    for other in ("two_valued", "latest_version", "trust_statements",
                  "no_authorization", "no_number_check", "no_kind_agreement"):
        _, from_model = _realize("You will be refunded 900 rupees.", conclusion,
                                 frozenset({other}))
        assert from_model is False, f"{other} wrongly disabled the figure check"


# --------------------------------------------------------------------------
# the switch machinery itself
# --------------------------------------------------------------------------

def test_switches_are_scoped_to_the_block():
    assert ablations.active("no_verification") is False
    with ablations.applied(frozenset({"no_verification"})):
        assert ablations.active("no_verification") is True
    assert ablations.active("no_verification") is False


def test_unknown_switch_is_rejected():
    """A typo must fail loudly rather than silently ablating nothing."""
    with pytest.raises(ValueError, match="unknown ablation"):
        with ablations.applied(frozenset({"no_verifcation"})):  # deliberate typo
            pass


def test_no_verification_reaches_every_guard_it_must():
    """The switch is only informative if it reaches all three layers.

    Counting them is a structural check, not a behavioural one, but it is the check
    that would have caught the defect: two reruns of the controlled ablation produced
    byte-identical arms because the switch covered one layer, then two, of three.
    """
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    hits = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if 'active("no_verification")' in text:
            hits.append((path.name, text.count('active("no_verification")')))
    total = sum(n for _, n in hits)
    assert total >= 3, (
        f"no_verification is read {total} time(s) in {hits}; it must gate the realizer's "
        "figure check, the re-verification fallback and the release decision")
    files = {name for name, _ in hits}
    assert {"controller.py", "response_generation.py"} <= files, (
        f"expected both the controller and the realizer to consult it, got {files}")


def test_every_declared_switch_is_read_somewhere():
    """Each name in KNOWN must be consulted by some module.

    A declared switch that nothing reads is the same bug in a different place: the
    benchmark would run an ablation arm that changes no behaviour at all.
    """
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    sources = "\n".join(p.read_text(encoding="utf-8", errors="ignore")
                        for p in root.rglob("*.py"))
    unread = [name for name in ablations.KNOWN
              if f'active("{name}")' not in sources]
    assert not unread, f"declared but never read: {unread}"
