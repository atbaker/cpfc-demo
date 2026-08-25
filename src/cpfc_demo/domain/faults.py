import hashlib


def deterministic_percent(*, seed: int, order_sequence: int, service: str, attempt: int) -> int:
    value = f"{seed}:{order_sequence}:{service}:{attempt}".encode()
    digest = hashlib.sha256(value).digest()
    return int.from_bytes(digest[:4], "big") % 100


def should_fail(*, percentage: int, seed: int, order_sequence: int, service: str, attempt: int) -> bool:
    return percentage > deterministic_percent(
        seed=seed,
        order_sequence=order_sequence,
        service=service,
        attempt=attempt,
    )
