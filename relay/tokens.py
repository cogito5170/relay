"""토크나이저 없이 쓰는 대략적인 토큰 추정.

영문/코드는 약 4자당 1토큰, 한글 등 비ASCII 문자는 1자당 약 1토큰으로 본다.
정확한 값이 아니라 "이 pack 이 예산 안에 들어가는가"를 판단하기 위한 용도다.
"""


def estimate(text: str) -> int:
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    return -(-ascii_chars // 4) + (len(text) - ascii_chars)


def human(n: float) -> str:
    n = float(n)
    for unit, div in (("M", 1_000_000), ("k", 1_000)):
        if abs(n) >= div:
            return f"{n / div:.1f}{unit}"
    return f"{n:.0f}"
