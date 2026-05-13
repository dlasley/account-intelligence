def compute_overall_health(
    dimension_scores: list[tuple[float, int]],  # (weight, score) for enabled dims
) -> int | None:
    """Weighted average of enabled dimension scores, clamped 1-100. None if no scores."""
    if not dimension_scores:
        return None
    total_weight = sum(w for w, _ in dimension_scores)
    if total_weight == 0:
        return None
    raw = sum(w * s for w, s in dimension_scores) / total_weight
    return max(1, min(100, round(raw)))
