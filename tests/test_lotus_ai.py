"""莲花麻将（lotus-legacy）策略 AI 单元测试 —— 对照 src/game/variants/lotus/lotusAi.ts。

验证与经典 AI 的关键差异：副露决策按听牌质量取舍（不提升则 pass），
弃牌启发式按听口/剩余张/安全度打分（癞子保手）。
"""

from app.core.lotus_ai import choose_discard_index, decide_claim


class TestLotusDecideClaim:
    """对应 lotusAi.ts decideClaim（策略副露决策）。"""

    def test_gang_when_can_gang(self):
        """能杠必杠（杠后从牌尾补牌，无法凭当前手牌准确判断，保留最高优先级）。"""
        hand = ['east', 'east', 'east', 'm1', 'm2', 'm3', 'p1', 'p2', 'p3',
                's1', 's2', 's3', 'north']
        decision = decide_claim({
            'hand': hand, 'exposedMelds': 0, 'jokers': [],
            'tile': 'east', 'canGang': True, 'canPeng': True, 'chiOptions': [],
        })
        assert decision == {'kind': 'gang'}

    def test_pass_when_peng_breaks_tenpai(self):
        """碰牌会破坏听牌时 pass（经典 AI 会无脑碰，这是关键差异）。"""
        hand = ['m1', 'm2', 'm3', 'm4', 'm5', 'm6', 'p1', 'p2', 'p3',
                's1', 's2', 'east', 'east']
        decision = decide_claim({
            'hand': hand, 'exposedMelds': 0, 'jokers': [],
            'tile': 'east', 'canGang': False, 'canPeng': True, 'chiOptions': [],
        })
        assert decision == {'kind': 'pass'}


class TestLotusChooseDiscardIndex:
    """对应 lotusAi.ts chooseDiscardIndex（质量打分弃牌）。"""

    def test_joker_kept(self):
        """白板（癞子）保手，优先打其它孤张。"""
        hand = ['white', 's9', 's9', 'm7']
        index = choose_discard_index(hand, [], random=lambda: 0)
        assert hand[index] == 'm7'
