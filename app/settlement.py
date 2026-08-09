"""纯分数结算服务。

calculate_* 只产生流水，不修改玩家；apply_deltas 是唯一状态写入口。
"""

from dataclasses import dataclass
from typing import Optional

from app.models.game import GamePlayer


def _is_integer(value) -> bool:
    return isinstance(value, int) or (isinstance(value, float) and value.is_integer())


@dataclass(frozen=True)
class SettlementResult:
    deltas: tuple[dict, ...]
    total_won: int = 0

    def as_list(self) -> list[dict]:
        return [dict(delta) for delta in self.deltas]


class SettlementService:
    def calculate_kong(
        self,
        player_count: int,
        kong_player_index: int,
        type_: str,
        base_score: int,
        from_index: Optional[int] = None,
    ) -> SettlementResult:
        payers = [from_index] if type_ == 'discard' else [
            i for i in range(player_count) if i != kong_player_index
        ]
        payment = base_score * 2 if type_ == 'concealed' else base_score
        valid_payers = [
            payer for payer in payers
            if _is_integer(payer) and payer != kong_player_index
        ]
        deltas = (
            {'playerIndex': kong_player_index, 'amount': payment * len(valid_payers)},
            *(
                {'playerIndex': payer, 'amount': -payment}
                for payer in valid_payers
            ),
        )
        return SettlementResult(tuple(d for d in deltas if d['amount'] != 0))

    def calculate_win(
        self,
        player_count: int,
        winner_index: int,
        points: int,
        payer_index: Optional[int] = None,
        dealer_index: Optional[int] = None,
    ) -> SettlementResult:
        payers = [payer_index] if _is_integer(payer_index) else [
            i for i in range(player_count) if i != winner_index
        ]
        amounts = [
            points * 2 if (winner_index != dealer_index and payer == dealer_index) else points
            for payer in payers
        ]
        total_won = sum(amounts)
        deltas = (
            {'playerIndex': winner_index, 'amount': total_won},
            *(
                {'playerIndex': payer, 'amount': -amount}
                for payer, amount in zip(payers, amounts)
            ),
        )
        return SettlementResult(
            tuple(delta for delta in deltas if delta['amount'] != 0),
            total_won=total_won,
        )

    @staticmethod
    def apply_deltas(players: list[GamePlayer], deltas: tuple[dict, ...] | list[dict]) -> None:
        for delta in deltas:
            players[delta['playerIndex']].score += delta['amount']


settlement_service = SettlementService()
