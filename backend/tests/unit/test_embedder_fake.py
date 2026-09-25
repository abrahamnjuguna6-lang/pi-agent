"""T7.1: the fake embedder must be deterministic, so vector tests are reproducible (design §23.3)."""

import math

import pytest

from lifeos.domain.memory.embeddings import FakeEmbedder, fake_vector, unit_vector


async def test_the_same_text_always_gives_the_same_vector() -> None:
    first = await FakeEmbedder(dimensions=32).embed(["deep work in the morning"])
    second = await FakeEmbedder(dimensions=32).embed(["deep work in the morning"])
    assert first == second  # stable across instances, and across processes (seeded by SHA-256)


async def test_different_texts_give_different_vectors() -> None:
    embedder = FakeEmbedder(dimensions=32)
    [first, second] = await embedder.embed(["running", "reading"])
    assert first != second
    assert abs(sum(a * b for a, b in zip(first, second, strict=True))) < 0.6  # near-orthogonal


def test_vectors_are_normalized() -> None:
    vector = fake_vector("anything", 64)
    assert math.isclose(math.sqrt(sum(value * value for value in vector)), 1.0, rel_tol=1e-9)
    assert len(vector) == 64


def test_overrides_pin_exact_vectors() -> None:
    axis = unit_vector(3, 8)
    assert axis == [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0]


async def test_overrides_are_returned_verbatim() -> None:
    axis = unit_vector(1, 8)
    embedder = FakeEmbedder(dimensions=8, overrides={"focus": axis})
    assert await embedder.embed(["focus", "other"]) == [axis, fake_vector("other", 8)]


async def test_fail_on_raises_for_the_matching_text() -> None:
    embedder = FakeEmbedder(dimensions=8, fail_on=["boom"])
    with pytest.raises(RuntimeError):
        await embedder.embed(["fine", "this one goes boom"])
    assert await embedder.embed(["fine"]) == [fake_vector("fine", 8)]
