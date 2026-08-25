from cpfc_demo.domain.faults import deterministic_percent, should_fail


def test_faults_are_deterministic() -> None:
    first = deterministic_percent(seed=1861, order_sequence=42, service="ticket", attempt=1)
    second = deterministic_percent(seed=1861, order_sequence=42, service="ticket", attempt=1)

    assert first == second
    assert 0 <= first < 100


def test_fault_percentage_boundaries() -> None:
    assert not should_fail(percentage=0, seed=1861, order_sequence=1, service="payment", attempt=1)
    assert should_fail(percentage=100, seed=1861, order_sequence=1, service="payment", attempt=1)


def test_retry_attempt_can_change_outcome() -> None:
    values = {
        deterministic_percent(seed=1861, order_sequence=8, service="reservation", attempt=attempt)
        for attempt in range(1, 10)
    }

    assert len(values) > 1
