"""玩法规则集注册表。每个房间独立创建规则集，避免翻精状态跨房间污染。"""

from app.rules.base import GameRuleSet
from app.rules.lianhua import LianhuaGuangmaRuleSet
from app.rules.lotus_legacy import LotusLegacyRuleSet

RULESET_IDS = ('lotus-classic', 'lotus-legacy')


def get_rule_set(ruleset_id: str | None = None) -> GameRuleSet:
    if ruleset_id in (None, '', 'lotus-classic', 'lianhua_guangma'):
        return LianhuaGuangmaRuleSet()
    if ruleset_id == 'lotus-legacy':
        return LotusLegacyRuleSet()
    raise ValueError(f'UNKNOWN_RULESET:{ruleset_id}')
