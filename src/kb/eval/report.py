import random
import statistics


def bootstrap_mean(values: list[float], samples: int, seed: int) -> dict:
    if not values or samples < 2:
        raise ValueError("需要非空指标和至少两次 bootstrap")
    rng = random.Random(seed)
    means = sorted(statistics.mean(rng.choices(values, k=len(values))) for _ in range(samples))
    return {
        "mean": statistics.mean(values),
        "low": means[int(samples * 0.025)],
        "high": means[min(samples - 1, int(samples * 0.975))],
        "n": len(values),
    }
