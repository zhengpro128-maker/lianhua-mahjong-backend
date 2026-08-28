"""基于公开局面的条件深度思考触发器；不保存或展示模型思考过程。"""

from dataclasses import dataclass, field
import random as random_module


@dataclass(frozen=True)
class TriggerConfig:
    candidate_score_gap: float = 8
    late_wall_count: int = 12
    opponent_threat: int = 70
    score_swing: int = 800


@dataclass(frozen=True)
class ConditionalReasoningConfig:
    enabled: bool = True
    max_per_round: int = 2
    max_per_match: int = 8
    deadline_ms: int = 40000
    min_remaining_budget_ms: int = 45000
    trigger: TriggerConfig = field(default_factory=TriggerConfig)
    audit_sample_rate: float = 0.02


DEFAULT_CONDITIONAL_REASONING = ConditionalReasoningConfig()


def _band(value, high, medium, low=0):
    if value in ('高', '优'):
        return high
    if value == '中':
        return medium
    return low


def candidate_score(candidate: dict, rule_code: str) -> float:
    feat = candidate.get('features') or {}
    score = 50.0
    if isinstance(feat.get('shanten'), (int, float)):
        score -= feat['shanten'] * 14
    if isinstance(feat.get('ukeire'), (int, float)):
        score += min(24, feat['ukeire'] * 2)
    if isinstance(feat.get('effectiveRemaining'), (int, float)):
        score += min(20, feat['effectiveRemaining'])
    score += _band(feat.get('efficiency'), 12, 4, -8)
    if rule_code != 'lotus-classic':
        score += _band(feat.get('safety'), 10, 2, -8)
    score += _band(feat.get('scoreDeltaBand'), 14, 7)
    if feat.get('ready') is True:
        score += 18
    score -= len(feat.get('risks') or []) * 8
    if (candidate.get('action') or {}).get('kind') == 'pass':
        score -= 2
    return score


def _candidate_gap(request: dict) -> float:
    scores = sorted((candidate_score(c, request.get('ruleCode', ''))
                     for c in request.get('candidates', [])), reverse=True)
    return abs(scores[0] - scores[1]) if len(scores) >= 2 else float('inf')


def _suit(tile: str):
    return tile[-1] if tile and tile[-1] in ('万', '筒', '条') else None


def _opponent_threat(request: dict) -> int:
    if request.get('ruleCode') == 'lotus-classic':
        return 0
    state = request.get('state') or {}
    snapshots = state.get('snapshots') or {}
    largest = 0
    for key in ('upper', 'opposite', 'lower'):
        view = snapshots.get(key) or {}
        melds = view.get('melds') or []
        exposed = [tile for meld in melds for tile in (meld.get('tiles') or [])]
        suits = [suit for suit in (_suit(tile) for tile in exposed) if suit]
        dominant = max((suits.count(suit) for suit in set(suits)), default=0) / len(suits) if suits else 0
        value = len(melds) * 20
        if len(melds) >= 2 and dominant >= .75:
            value += 22
        if state.get('wallCount', 99) <= 24:
            value += 10
        if len(melds) >= 2 and len(view.get('discards') or []) <= 7:
            value += 8
        largest = max(largest, min(100, value))
    return largest


def _score_swing(request: dict) -> int:
    candidates = request.get('candidates') or []
    return max(((c.get('features') or {}).get('scoreDelta') or 0
                for c in candidates), default=0)


class ConditionalReasoningCoordinator:
    def __init__(self, config=DEFAULT_CONDITIONAL_REASONING, random_fn=None):
        self.config = config
        self.random = random_fn or random_module.random
        self.match_uses = 0
        self.round_uses = {}

    def admit(self, request: dict, remaining_budget_ms: float) -> bool:
        cfg = self.config
        state = request.get('state') or {}
        round_index = int(state.get('roundIndex') or 0)
        triggered = (
            _candidate_gap(request) <= cfg.trigger.candidate_score_gap
            or state.get('wallCount', 99) <= cfg.trigger.late_wall_count
            or _opponent_threat(request) >= cfg.trigger.opponent_threat
            or _score_swing(request) >= cfg.trigger.score_swing
            or self.random() < cfg.audit_sample_rate
        )
        allowed = (cfg.enabled and triggered
                   and remaining_budget_ms >= cfg.min_remaining_budget_ms
                   and self.match_uses < cfg.max_per_match
                   and self.round_uses.get(round_index, 0) < cfg.max_per_round)
        if allowed:
            self.match_uses += 1
            self.round_uses[round_index] = self.round_uses.get(round_index, 0) + 1
        return allowed

    def reset(self):
        self.match_uses = 0
        self.round_uses.clear()
