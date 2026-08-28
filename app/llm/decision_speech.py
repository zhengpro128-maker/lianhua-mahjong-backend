"""牌桌自由台词清洗与动作兜底台词。"""

import re

from app.llm.speech_policy import compact_speech_text

DECISION_SPEECH_LINES = {
    'discard': {
        '激进': ('这张不要了。', '先打出去。', '这一张走。'),
        '稳健': ('这张先走。', '先打这一张。', '按牌路来。'),
        '话痨': ('先把这张放出去。', '这一张先打掉。', '轮到我出牌啦。'),
        '高冷': ('打。', '这张。', '出牌。'),
    },
    'gang': {
        '激进': ('大明杠，开！', '这杠我拿了！', '直接杠！'),
        '稳健': ('大明杠。', '这杠可以开。', '顺势开杠。'),
        '话痨': ('这张正好大明杠！', '来得巧，我杠了！', '四张齐了，开杠！'),
        '高冷': ('杠。', '大明杠。', '开杠。'),
    },
    'peng': {
        '激进': ('碰！', '这张我要了！', '直接碰！'),
        '稳健': ('碰一个。', '这张可以碰。', '顺势碰。'),
        '话痨': ('来得正好，我碰！', '这张我可要碰啦！', '凑齐了，碰一个！'),
        '高冷': ('碰。', '收下。', '碰了。'),
    },
    'chi': {
        '激进': ('直接吃！', '这张我吃了！', '顺手拿下！'),
        '稳健': ('顺手吃了。', '这张可以吃。', '吃一组。'),
        '话痨': ('刚好连上，我吃啦！', '这张来得正合适！', '顺子齐了，吃一个！'),
        '高冷': ('吃。', '收下。', '吃了。'),
    },
    'pass': {
        '激进': ('先放你一手。', '这次不要。', '继续来。'),
        '稳健': ('先看看。', '这次先过。', '不急这一手。'),
        '话痨': ('这张我先不要啦。', '你们继续，我看看。', '先过，后面再说！'),
        '高冷': ('过。', '不要。', '继续。'),
    },
    'added-kong': {
        '激进': ('补杠，开！', '这张补上！', '补杠拿下！'),
        '稳健': ('补杠。', '顺势补杠。', '这一张补上。'),
        '话痨': ('第四张到了，补杠！', '刚好补上这一杠！', '等到了，补杠啦！'),
        '高冷': ('补杠。', '补上。', '杠。'),
    },
    'concealed-kong': {
        '激进': ('暗杠，开！', '四张在手，杠！', '直接暗杠！'),
        '稳健': ('暗杠。', '这手开暗杠。', '暗杠正合适。'),
        '话痨': ('四张都在手，暗杠！', '藏得好好的，开杠啦！', '这一组正好暗杠！'),
        '高冷': ('暗杠。', '杠。', '开。'),
    },
    'wind-kong': {
        '激进': ('乱风杠，开！', '四风齐了！', '风杠拿下！'),
        '稳健': ('乱风杠。', '四风成杠。', '这一手风杠。'),
        '话痨': ('东南西北齐了，风杠！', '四风都到手啦！', '这手正好乱风杠！'),
        '高冷': ('风杠。', '四风齐。', '杠。'),
    },
    'win': {
        '激进': ('拿下！',), '稳健': ('收下了。',), '话痨': ('这手我拿下啦！',), '高冷': ('胡。',),
    },
}


def decision_speech(action: dict, style: str, sequence: int = 0) -> str:
    kind = action.get('kind', 'discard')
    styles = DECISION_SPEECH_LINES.get(kind, DECISION_SPEECH_LINES['discard'])
    variants = styles.get(style, styles['稳健'])
    return variants[abs(sequence) % len(variants)]


def resolve_decision_speech(message: str, action: dict,
                            style: str, sequence: int = 0,
                            facts: dict | None = None) -> str:
    """保留合规烟雾弹；仅缺失或幕后内容回退程序台词。"""
    compact = compact_speech_text(message)
    facts = facts or {}
    denies_dealer = bool(re.search(r'我(?:可|并)?不是庄家|我非庄家|我不坐庄', compact))
    claims_dealer = bool(re.search(
        r'本庄|庄家(?:是|就是)我|我(?:可是|就是|是|当|来当|在当|要当|坐|来坐|在坐)庄家?|这把我坐庄|我是东家',
        compact))
    is_dealer = facts.get('isDealer')
    contradicts_dealer = (is_dealer is False and claims_dealer and not denies_dealer) \
        or (is_dealer is True and denies_dealer)
    claimed_action = 'chi' if re.search(r'吃定了|我要吃|我吃了|这牌我吃|直接吃', compact) else \
        'peng' if re.search(r'我要碰|我碰了|碰一个|直接碰|这牌我碰', compact) else \
        'gang' if re.search(r'我要杠|我杠了|开杠|大明杠|暗杠|补杠|风杠|直接杠', compact) else \
        'pass' if re.search(r'我过了|这次我过|我要过', compact) else None
    actual_kind = action.get('kind')
    action_matches = claimed_action is None or claimed_action == actual_kind \
        or (claimed_action == 'gang' and actual_kind in (
            'gang', 'added-kong', 'concealed-kong', 'wind-kong'))
    if compact and not contradicts_dealer and action_matches:
        return compact
    return decision_speech(action, style, sequence)
