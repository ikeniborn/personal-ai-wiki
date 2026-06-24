import pytest

from paw.vector.reindex import plan_batches


def test_plan_batches_splits_with_remainder():
    assert plan_batches(250, 100) == [100, 100, 50]


def test_plan_batches_exact_multiple():
    assert plan_batches(200, 100) == [100, 100]


def test_plan_batches_empty_for_zero_or_negative_total():
    assert plan_batches(0, 100) == []
    assert plan_batches(-5, 100) == []


def test_plan_batches_rejects_bad_batch_size():
    with pytest.raises(ValueError):
        plan_batches(10, 0)
