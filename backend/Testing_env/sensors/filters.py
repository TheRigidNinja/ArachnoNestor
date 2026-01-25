"""Signal filters (placeholders for future use)."""


def low_pass(value, prev, alpha=0.2):
    return prev + alpha * (value - prev)
