"""规范动作合法性校验 —— §8.2 表（控制器侧；引擎执行层还有二次验牌/动作执行校验）。"""

from typing import Optional

from app.rules.base import GameRuleSet


def validate_action(ctx, action: dict, rules: GameRuleSet) -> bool:
    """逐类型校验 LLM 返回的动作；win 只允许引擎短路产生（这里一律拒绝）。"""
    kind = action.get('kind')
    hand = ctx.hand

    if kind == 'discard':
        index = action.get('handIndex')
        return isinstance(index, int) and 0 <= index < len(hand)
    if kind == 'added-kong':
        index = action.get('meldIndex')
        if not isinstance(index, int) or index < 0 or index >= len(ctx.melds):
            return False
        meld = ctx.melds[index]
        if getattr(meld, 'type', None) != 'peng' or not rules.can_added_kong(hand, ctx.melds, meld.tile):
            return False
        return True
    if kind == 'concealed-kong':
        tile = action.get('tile')
        return tile is not None and tile in rules.concealed_kongs(hand)
    if kind == 'wind-kong':
        wind_kong = getattr(rules, 'wind_kong', None)
        return rules.code == 'lotus-legacy' and wind_kong is not None and wind_kong(hand)
    if kind == 'gang':
        capabilities = rules.claim_capabilities(hand, ctx.tile) if getattr(ctx, 'tile', None) else None
        return capabilities is not None and capabilities.can_gang
    if kind == 'peng':
        capabilities = rules.claim_capabilities(hand, ctx.tile) if getattr(ctx, 'tile', None) else None
        return capabilities is not None and capabilities.can_peng
    if kind == 'chi':
        option_index = action.get('optionIndex')
        options = list(getattr(ctx, 'chiOptions', None) or getattr(ctx, 'chi_options', None) or [])
        return isinstance(option_index, int) and 0 <= option_index < len(options)
    if kind == 'pass':
        return True
    return False
