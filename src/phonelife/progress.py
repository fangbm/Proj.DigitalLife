"""Conservative feedback-based stagnation detection, without model declarations."""
from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass

from PIL import Image


@dataclass(frozen=True)
class ProgressAssessment:
    verdict: str  # progressed / stagnant / uncertain
    reason: str
    stable_fraction: float
    changed_fraction: float
    changed_tiles: int
    before_sha256: str
    after_sha256: str
    content_before_sha256: str
    content_after_sha256: str
    region: tuple[int, int, int, int]
    detector_version: str = "stable-body-tiles/2"


class ActionProgressDetector:
    """Compare temporally stable pixels across a spread of content tiles.

    Top 8%, bottom 8%, and side 5% are excluded (system bars/edge indicators).
    A second pre- and post-action sample masks animation within the body.
    No reliable evidence of change means no progress, never guessed progress.
    """
    grid_width, grid_height = 72, 96

    def _body(self, observation):
        region = (int(observation.width * .05), int(observation.height * .08),
                  int(observation.width * .95), int(observation.height * .92))
        with Image.open(io.BytesIO(observation.png)) as image:
            if image.size != (observation.width, observation.height):
                raise ValueError("Decoded image size differs from observation")
            image = image.convert("L").crop(region).resize(
                (self.grid_width, self.grid_height), Image.Resampling.BOX)
            pixels = image.tobytes()
        return pixels, region

    def assess(self, action, before, after) -> ProgressAssessment:
        first, pre = before
        post, last = after
        empty = ("", "", "", "", (0, 0, 0, 0))
        if len({(o.width, o.height) for o in (first, pre, post, last)}) != 1:
            return ProgressAssessment("uncertain", "Screen dimensions changed", 0, 0, 0, *empty)
        try:
            a, region = self._body(first)
            b, _ = self._body(pre)
            c, _ = self._body(post)
            d, _ = self._body(last)
        except (OSError, ValueError):
            return ProgressAssessment("uncertain", "Content image could not be decoded", 0, 0, 0, *empty)
        stable = changed = 0
        tiles = {}
        for index, (v1, v2, v3, v4) in enumerate(zip(a, b, c, d)):
            if abs(v1 - v2) > 6 or abs(v3 - v4) > 6:
                continue
            stable += 1
            if abs(v2 - v4) >= 12:
                changed += 1
                tile = (index % self.grid_width // 9, index // self.grid_width // 12)
                tiles[tile] = tiles.get(tile, 0) + 1
        spread = {tile for tile, count in tiles.items() if count >= 3}
        stable_fraction = stable / len(a)
        changed_fraction = changed / len(a)
        # A local spinner/blinking widget is not navigation/scrolling progress,
        # including animations whose two samples happen to land on a quiet phase.
        enough = (changed_fraction >= .012 and len(spread) >= 4
                  and len({t[1] for t in spread}) >= 4 and len({t[0] for t in spread}) >= 3)
        verdict = "uncertain" if stable_fraction < .65 else "progressed" if enough else "stagnant"
        return ProgressAssessment(verdict,
            "Stable distributed body change" if verdict == "progressed" else
            "Body dominated by temporal changes" if verdict == "uncertain" else "No meaningful stable body change",
            stable_fraction, changed_fraction, len(spread), pre.sha256, last.sha256,
            hashlib.sha256(b).hexdigest(), hashlib.sha256(d).hexdigest(), region)


class StagnationGuard:
    """The original two ineffective dispatches budget; progress resets the counter."""
    def __init__(self):
        self.signature = None
        self.ineffective = 0

    @staticmethod
    def signature_of(action):
        return json.dumps({k: v for k, v in action.wire().items() if k != "reason"}, sort_keys=True)

    def should_stop(self, action) -> bool:
        return self.signature == self.signature_of(action) and self.ineffective >= 2

    def record(self, action, assessment: ProgressAssessment):
        signature = self.signature_of(action)
        self.ineffective = (self.ineffective if signature == self.signature else 0) + 1
        if assessment.verdict == "progressed":
            self.ineffective = 0
        self.signature = signature
