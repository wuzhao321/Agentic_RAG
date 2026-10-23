from rapidfuzz import fuzz

DOC_TYPES = ("技术规范", "交付报告", "接口说明书")


def route(query: str, projects: list[str], config: dict) -> dict:
    result = {}
    types = [t for t in DOC_TYPES if t in query]
    if len(types) == 1:
        result["doc_type"] = types[0]
    matches = [(p, 100.0 if p in query else fuzz.partial_ratio(p, query)) for p in projects]
    matches = [
        (p, s)
        for p, s in matches
        if s >= config["project_match_score"] and s / 100 >= config["min_confidence"]
    ]
    # Multiple project mentions should never be narrowed to just one project.
    if len(matches) == 1:
        result["project_name"] = matches[0][0]
    return result
