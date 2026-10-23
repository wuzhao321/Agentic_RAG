from copy import deepcopy
from itertools import product


def configurations(base: dict, *, grid: dict | None = None, final: bool = False) -> list[tuple]:
    if final and grid:
        raise ValueError("留出集禁止网格搜索")
    if grid:
        return [
            (
                f"D_n{n}_k{k}_prefix{int(prefix)}",
                {
                    **deepcopy(base),
                    "mode": "D",
                    "recall_top_n": n,
                    "rrf_k": k,
                    "use_query_prefix": prefix,
                },
            )
            for n, k, prefix in product(
                grid["recall_top_n"], grid["rrf_k"], grid["use_query_prefix"]
            )
        ]
    return [
        (mode, {**deepcopy(base), "mode": mode})
        for mode in (["A", "D"] if final else ["A", "B", "C", "D"])
    ]
