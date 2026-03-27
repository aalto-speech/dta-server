# Analytics Comparison API — Extension Points & Future Work

This guide documents the design extensibility of the user-vs-cohort comparison analytics feature implemented in feat/comparison branch. The current Phase 3 implementation provides a stable foundation for future enhancements.

## Current Implementation

### Supported Cohort Strategy

- **Self-Assessment Level** (`cohort_type: "self_assessment"`)
  - Users grouped by their self-reported Finnish proficiency (CEFR: A1, A2, B1, B2, C1+)
  - File: [app/db.py](app/db.py#L146) → `get_comparison_stats_by_self_assessment()`
  - Endpoint: `GET /analytics/comparison?guid=...&days=...`

### Response Schema

- **comparisonAvailable**: Privacy-gated flag; false if cohort size < threshold
- **percentile**: Numeric percentile (0-100) with 2 decimal precision as primary metric
- **rankBand**: Nullable string (e.g., "top 10%") — deferred for UI adoption
- **distributionSummary**: Aggregate bucket counts (no individual scores exposed)

### Privacy Guarantees

- Minimum cohort size threshold (`SETTINGS.minimum_cohort_size`, default: 10)
- No per-user raw data in response payload
- Configurable time window filtering (`days` parameter)
- Percentile computation prevents re-identification (only count-based aggregation)

---

## Future Enhancement: Performance Band Cohorts

### Design Sketch

**Objective**: Allow users to compare against peers with similar ASA-inferred performance levels, not just self-assessment.

**Implementation Steps**:

1. **Define Performance Band Enum** (in `app/models/onboarding.py` or new file)

   ```python
   class PerformanceBand(StrEnum):
       HIGH = "high"          # e.g., avg score >= 4.0
       MEDIUM_HIGH = "medium_high"  # 3.0-4.0
       MEDIUM = "medium"      # 2.0-3.0
       MEDIUM_LOW = "medium_low"  # 1.0-2.0
       LOW = "low"            # < 1.0
   ```

2. **Infer Band from User's Historical Average** (in `app/db.py`)

   ```python
   def infer_performance_band(guid: UUID) -> PerformanceBand | None:
       """Classify user based on their all-time average score."""
       avg = get_user_average_score(guid)
       if avg is None:
           return None
       # Map avg to band based on thresholds
   ```

3. **Implement Cohort Query** (in `app/db.py`)

   ```python
   def get_comparison_stats_by_performance_band(
       guid: UUID,
       days: int | None = None,
   ) -> ComparisonStats:
       """Similar to get_comparison_stats_by_self_assessment but groups by inferred band."""
       band = infer_performance_band(guid)
       # Query all users with same band + sufficient data
       # Return ComparisonStats with cohort_type="performance_band"
   ```

4. **Update Endpoint Routing** (in `app/main.py`)

   ```python
   @app.get("/analytics/comparison")
   async def analytics_comparison(
       guid: str,
       days: int | None = None,
       cohort_type: str | None = None,  # "self_assessment" | "performance_band"
   ) -> JSONResponse:
       """Route to appropriate cohort strategy."""
       cohort_type = cohort_type or "self_assessment"

       if cohort_type == "performance_band":
           stats = get_comparison_stats_by_performance_band(query.guid, query.days)
       else:
           stats = get_comparison_stats_by_self_assessment(query.guid, query.days)
       # ... rest of endpoint
   ```

5. **Test Coverage**
   - Mirror existing test suite for new cohort type
   - Verify privacy boundaries (minimum cohort, no raw data exposure)
   - Test edge cases: users at band boundaries, ties

---

## Future Enhancement: Distribution Bucket Privacy

### Design Sketch

**Objective**: Further refine privacy by suppressing distribution histograms when bucket sizes are too small (<5).

### Implementation Steps

1. **Add Configuration** (in `app/config.py`)

   ```python
   @dataclass(frozen=True)
   class Settings:
       # ... existing fields ...
       min_distribution_bucket_size: int = 5  # Suppress distribution if any bucket < this
   ```

2. **Update Distribution Filter** (in `app/db.py`)

   ```python
   def _get_distribution_summary(...) -> dict[str, int] | None:
       """Return None if any bucket count < min threshold to prevent bucket inference attacks."""
       buckets = { ... }
       if any(count < SETTINGS.min_distribution_bucket_size for count in buckets.values()):
           return None
       return buckets
   ```

3. **Update Aggregation Functions**
   - Ensure ComparisonStats respects distribution filtering
   - Document in function docstrings

---

## Future Enhancement: RankBand UI Adoption

### Design Sketch

**Current State**: `rankBand` field is nullable in response; always null.

**UI Adoption Timeline**:

1. **(Backend Enhancement)**

   ```python
   # In get_comparison_stats_by_self_assessment or new helper:
   def percentile_to_rank_band(percentile: float) -> str:
       """Derive human-readable band from numeric percentile."""
       if percentile >= 90:
           return "top 10%"
       elif percentile >= 70:
           return "top 30%"
       elif percentile >= 30:
           return "around average"
       else:
           return "bottom 30%"
   # Optionally populate response payload rankBand field
   ```

2. **(UI Migration)**
   - Update client to use `rankBand` for display if available; fall back to percentile.
   - No API change; client gracefully adopts new field when present.

---

## Testing Strategy for Extensions

Each new cohort strategy or privacy enhancement should include:

1. **Unit Tests** (DB layer)
   - Correctness: aggregates match expected values
   - Privacy: no raw user data leakage
   - Edge cases: empty cohort, ties, boundary values

2. **Integration Tests** (Endpoint layer)
   - Response contract validation
   - Authorization (user access check)
   - Error handling (invalid cohort type, missing parameter)

3. **Regression Tests**
   - Existing tests continue to pass
   - Privacy guarantees remain (minimum cohort, no exposure)

---

## API Versioning Considerations

**Current Endpoint**: `GET /api/v1/analytics/comparison`

**Stability Commitment**:

- `comparisonAvailable`, `cohortType`, `cohortLabel`, `cohortSize`, `userAverageScore`, `cohortAverage`, `percentile` are stable.
- `rankBand` and `distributionSummary` are optional/deferred; safe to populate without breaking changes.
- future `cohort_type` query parameter: backward compatible (default: "self_assessment").

**Breaking Changes** (if necessary in v2):

- Would occur in `/api/v2/analytics/comparison` endpoint
- Existing `/api/v1/` endpoint remains supported

---

## Branch & Commit History

- **feat/comparison**: Main feature branch for Phases 1-5
- **Phase 1 Commit**: `feat(analytics): add privacy-safe cohort comparison aggregations and tests`
- **Phase 2 Commit**: `(Aggregations + DB layer)`
- **Phase 3 Commit**: `feat(analytics): wire comparison endpoint with privacy-safe response mapping`
- **Phase 4 Commit**: `test(analytics): add comprehensive coverage for privacy regression and edge cases`
- **Phase 5 Commit**: `docs(analytics): add extension points for future cohort strategies and privacy enhancements`

---

## References

- Main implementation: [app/db.py](app/db.py) → `get_comparison_stats_by_self_assessment()`
- API contract: [app/models/analytics.py](app/models/analytics.py)
- Endpoint: [app/main.py](app/main.py) → `analytics_comparison()`
- Tests: [tests/test*analytics*\*.py](tests/)
- Config: [app/config.py](app/config.py) → `minimum_cohort_size`, `analytics_min_window_days`, `analytics_max_window_days`
