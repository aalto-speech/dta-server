"""Isotonic CEFR calibration, applied at inference.

WHY THIS IS PART OF THE MODEL AND NOT AN OPTIONAL POLISH. The scorer orders speakers well
(test Spearman 0.855) but compresses them toward the middle: predicted sd 0.393 against a
true 0.686. That is the expected behaviour of MSE on a skewed label distribution, and it is
a SCALE error — estimable from a dev set far too small to rank models. Correcting it on dev
and applying the correction at inference takes test RMSE from 0.4249 to 0.3854.

WHY ISOTONIC. Chosen from properties of the task, not from test scores:
  1. the correction must never reorder speakers -> monotone by construction;
  2. the compression is non-uniform (bias +0.567 at <A1/A1, -0.405 at B1) -> assume no shape;
  3. the tails are unconstrained at this dev size -> clip, never extrapolate.
Quadratic reaches a lower test RMSE and is rejected anyway: a parabola can turn around and
invert the ordering, which is the one thing the task forbids.

THE COST, stated plainly: isotonic cannot produce a value outside the range of dev labels it
was fitted on. `output_range` in assets/calibration.json is a hard ceiling and floor.
"""
import bisect
import json

from .config import CALIBRATION_JSON


class IsotonicCalibrator:
    """Piecewise-linear interpolation between fitted knots, clipped at both ends.

    This is exactly sklearn's IsotonicRegression.predict: it stores block boundaries and
    interpolates linearly between them. It is NOT a step function — a step implementation
    differs from sklearn by up to 0.25 on this data. Reimplemented (30 lines) so the server
    does not need scikit-learn; export_assets.py asserts the two agree to 1e-12.
    """

    def __init__(self, knots_x: list[float], knots_y: list[float], meta: dict | None = None):
        if len(knots_x) != len(knots_y) or len(knots_x) < 2:
            raise ValueError("calibrator needs >=2 matched knots")
        if any(b < a for a, b in zip(knots_x, knots_x[1:])):
            raise ValueError("knots_x must be non-decreasing")
        if any(b < a for a, b in zip(knots_y, knots_y[1:])):
            raise ValueError("knots_y must be non-decreasing (isotonic)")
        self.x = list(map(float, knots_x))
        self.y = list(map(float, knots_y))
        self.meta = meta or {}

    @classmethod
    def load(cls, path=CALIBRATION_JSON) -> "IsotonicCalibrator":
        d = json.loads(path.read_text())
        if d.get("method") != "isotonic":
            raise ValueError(f"unsupported calibration method {d.get('method')!r}")
        return cls(d["knots_x"], d["knots_y"], meta=d)

    @property
    def output_range(self) -> tuple[float, float]:
        return self.y[0], self.y[-1]

    def __call__(self, value: float) -> float:
        v = float(value)
        if v <= self.x[0]:
            return self.y[0]
        if v >= self.x[-1]:
            return self.y[-1]
        i = bisect.bisect_right(self.x, v)
        x0, x1, y0, y1 = self.x[i - 1], self.x[i], self.y[i - 1], self.y[i]
        if x1 == x0:
            return y1
        return y0 + (y1 - y0) * (v - x0) / (x1 - x0)

    def clipped(self, value: float) -> bool:
        """True when the raw prediction fell outside the fitted range, so the returned score
        is a boundary value rather than a measurement. Surfaced in the API response."""
        return not (self.x[0] < float(value) < self.x[-1])
