"""Unit checks for the vocab joiner. Rule: cleanup may never reword the speaker.

Run: .venv/bin/python -m pytest -q engine/test_vocab.py

The terms below are made up. The module's CORE list is replaced for the duration of these tests
with three shapes the joiner knows: a CamelCase name with a digit inside ("Acme2Go"), a code of
capitals plus three digits ("ABCD101", "WXYZ311") and a plain name ("Zorp").
"""

import os
import tempfile

import pytest

from engine import vocab
from engine.vocab import VocabFixer, keep_vocab_term

TEST_CORE = ["Acme2Go", "ABCD101", "WXYZ311", "Zorp", "GitHub"]


@pytest.fixture(autouse=True, scope="module")
def _core():
    old = vocab.CORE
    vocab.CORE = TEST_CORE
    yield
    vocab.CORE = old


@pytest.fixture(scope="module")
def vf(_core):
    return VocabFixer(None)


@pytest.mark.parametrize("text", [
    "we finished phase 2 today",
    "stage 1 is done",
    "session 3 starts now",
    "fix B-5 next",
    "Phase 6 - next",
    "plan B 2 is fine",
    "his 311 points",
    "ask zorp about the acme",
    "version 2 of the deck",
    "B5 and b5 stay as typed",
])
def test_never_rewords(vf, text):
    assert vf.fix(text) == text


@pytest.mark.parametrize("text,want", [
    ("the Acme2 Go blog post", "the Acme2Go blog post"),
    ("ABCD 101 homework", "ABCD101 homework"),
    ("finish the ABCCD 101 homework.", "finish the ABCD101 homework."),
    ("WXYZ 311 and ABCD 101", "WXYZ311 and ABCD101"),
    ("the Acme2 Gp blog", "the Acme2Go blog"),
    ("acme 2 go", "Acme2Go"),
    ("Acme2 Go.", "Acme2Go."),
    ("push it to Git Hub", "push it to GitHub"),
])
def test_joins_real_terms(vf, text, want):
    assert vf.fix(text) == want


def test_near_miss_never_changes_a_digit(vf):
    assert vf.fix("Acme3 Go") == "Acme3 Go"
    assert vf.fix("Acme2 Gp") == "Acme2Go"


def test_lowercase_vocab_term_keeps_speakers_casing():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "vocab.txt")
        with open(p, "w") as f:
            f.write("k8s\nb5\nphase8\n")
        v = VocabFixer(p)
        assert v.fix("open K8 S now") == "open K8S now"
        assert v.fix("fix B-5 next") == "fix B-5 next"
        assert v.fix("phase 8 today") == "phase 8 today"


def test_vocab_filter():
    for bad in ["0.0.0.0", "12345", "555-123-4567", "a1b2c3d4e5f6", "c0ffee1",
                "local_3f2a9c1e-7b4d-4e2a-9c3b-1a2b3c4d5e6f", "3f2a9c1e-7b4d-4e2a-9c3b-1a2b3c4d5e6f",
                "v1.pdf", "jobs.example.com", "session8-setup.sh", "b5",
                "qzxwvbnmasdfghjklpoiuytr"]:
        assert not keep_vocab_term(bad), bad
    for good in ["acme2go", "crawl4ai", "k8s", "Acme2Go", "wxyz311"]:
        assert keep_vocab_term(good), good


def test_vocab_file_terms_are_added():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "vocab.txt")
        with open(p, "w") as f:
            f.write("Foo2Bar\n")
        v = VocabFixer(p)
        assert "Foo2Bar" in v.terms
        assert v.fix("the Foo 2 Bar app") == "the Foo2Bar app"
