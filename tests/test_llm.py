"""LLM 后端接入单元测试 —— 文档 §13（无网络：mock 供应商调用）。

覆盖：规范/解析、候选与特征、合法性校验、LLMPlayer 三路径（成功/非法/异常回退、
胡短路、skipDraw 约束）、房间装配（llmEnabled/能力探测）、局况元数据。
"""

import asyncio
from types import SimpleNamespace

import pytest

from app.game.llm_player import LLMPlayer
from app.game.player import ClaimContext, TurnContext
from app.llm.candidates import build_request
from app.llm.client import extract_json_object, parse_llm_output
from app.llm.validation import validate_action
from app.rules.registry import get_rule_set
from app.settlement import settlement_service


# ── 基础上下文工厂 ───────────────────────────────────────────

def run(coro):
    """在独立事件循环中执行协程（不依赖 pytest-asyncio 的全局循环状态）。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def turn_ctx(hand=None, melds=None, exposed_melds=0, skip_draw=False, **overrides):
    data = dict(
        hand=hand or ['m1', 'm2', 'm3', 'm4', 'm5', 'm6', 'm7', 'm8', 'm9',
                      'p1', 'p2', 'p3', 'p4', 'white'],
        melds=melds or [],
        exposedMelds=exposed_melds,
        kongBloom=False,
        skipDraw=skip_draw,
        afterKong=False,
        visibleTiles=[],
        publicTiles=[],
        wallCount=60,
        scores=[1000, 1000, 1000, 1000],
        peers=[{'discards': [], 'melds': []} for _ in range(4)],
        seatWind='东', roundWind='东', dealerIndex=0, roundIndex=1,
        requestId='turn-0-1', stateVersion='1:drawing:60:0:0:13',
        **overrides,
    )
    return TurnContext(**data)


def claim_ctx(hand=None, tile='m3', can_peng=True, can_gang=False, chi_options=None,
              can_hu=False, **overrides):
    data = dict(
        hand=hand or ['m3', 'm3', 'm5', 'm6', 'm7', 'm8', 'p2', 'p3', 'p4',
                      's1', 's2', 's3', 'east'],
        canGang=can_gang,
        canHu=can_hu,
        canPeng=can_peng,
        chiOptions=chi_options or [],
        tile=tile,
        from_=1,
        exposedMelds=0,
        visibleTiles=[],
        publicTiles=[],
        wallCount=60,
        scores=[1000, 1000, 1000, 1000],
        peers=[{'discards': [], 'melds': []} for _ in range(4)],
        seatWind='东', roundWind='东', dealerIndex=0, roundIndex=1,
        requestId='claim-1-2', stateVersion='1:checking:60:0:0:13',
        **overrides,
    )
    return ClaimContext(**data)


# ── 解析器 ───────────────────────────────────────────────────

class TestParsing:
    def test_balanced_brace_scanner_skips_strings(self):
        text = '好的 {"choice":"A1","message":"大{括}号"} 完毕'
        assert extract_json_object(text) == '{"choice":"A1","message":"大{括}号"}'

    def test_parse_valid_and_whitelist(self):
        assert parse_llm_output('{"choice":"A1","message":"哈哈哈"}', ['A1', 'A2']) == ('A1', '哈哈哈')
        with pytest.raises(Exception):
            parse_llm_output('{"choice":"A9"}', ['A1', 'A2'])


# ── 端点规范化（§7.2 安全约束）──────────────────────────────

class TestEndpoint:
    def test_remote_https_accepted(self):
        # 回归：远端 https 曾被误判为「协议不支持」（group(1) 不带冒号却比 'https:'）
        from app.llm.client import _normalize_endpoint
        assert _normalize_endpoint('https://api.deepseek.com/v1') == \
            'https://api.deepseek.com/v1/chat/completions'
        assert _normalize_endpoint('https://api.anthropic.com/v1/') == \
            'https://api.anthropic.com/v1/chat/completions'
        assert _normalize_endpoint('https://x.com/chat/completions') == \
            'https://x.com/chat/completions'

    def test_remote_http_rejected(self):
        from app.llm.client import _normalize_endpoint
        assert _normalize_endpoint('http://api.deepseek.com/v1') is None

    def test_localhost_http_allowed(self):
        from app.llm.client import _normalize_endpoint
        assert _normalize_endpoint('http://127.0.0.1:8000/v1') == \
            'http://127.0.0.1:8000/v1/chat/completions'
        assert _normalize_endpoint('http://localhost:4321') == \
            'http://localhost:4321/chat/completions'

    def test_userinfo_and_empty_rejected(self):
        from app.llm.client import _normalize_endpoint
        assert _normalize_endpoint('https://user:pass@host.com/v1') is None
        assert _normalize_endpoint('') is None
        assert _normalize_endpoint('ftp://host.com') is None


# ── 候选枚举 ─────────────────────────────────────────────────

class TestCandidates:
    def test_turn_discard_dedupe(self):
        ctx = turn_ctx(hand=['m3', 'm3', 'm5'])
        built = build_request(ctx, get_rule_set(), 'r1', 'v1', 'turn')
        labels = [c['label'] for c in built['request']['candidates'] if c['action']['kind'] == 'discard']
        assert labels == ['出3万', '出5万']
        assert built['engineSuggestion'] is not None

    def test_skip_draw_only_discard(self):
        ctx = turn_ctx(hand=['m3', 'm3', 'm3', 'm3'], melds=[], exposed_melds=0, skip_draw=True)
        built = build_request(ctx, get_rule_set(), 'r1', 'v1', 'turn')
        assert all(c['action']['kind'] == 'discard' for c in built['request']['candidates'])

    def test_claim_candidates_pass_gang_peng(self):
        ctx = claim_ctx(tile='m3', can_peng=True, can_gang=True)
        built = build_request(ctx, get_rule_set(), 'r1', 'v1', 'claim')
        assert [c['id'] for c in built['request']['candidates']] == ['Z', 'G', 'P']

    def test_lotus_wind_kong(self):
        rules = get_rule_set('lotus-legacy')
        rules.round_state.joker_tiles = []
        ctx = turn_ctx(hand=['east', 'south', 'west', 'north', 'm3', 'm3', 'm5'],
                       exposed_melds=0)
        built = build_request(ctx, rules, 'r1', 'v1', 'turn')
        kinds = [c['action']['kind'] for c in built['request']['candidates']]
        assert 'wind-kong' in kinds

    def test_claim_chi_options(self):
        rules = get_rule_set('lotus-legacy')
        rules.round_state.joker_tiles = []
        ctx = claim_ctx(hand=['m4', 'm5', 'east'], tile='m3', can_peng=False,
                        chi_options=[{'tiles': ['m3', 'm4', 'm5'], 'kind': 'sequence'}])
        built = build_request(ctx, rules, 'r1', 'v1', 'claim')
        ids = [c['id'] for c in built['request']['candidates']]
        assert 'C1' in ids


# ── 合法性校验（§8.2）──────────────────────────────────────

class TestValidation:
    def test_discard_out_of_range(self):
        ctx = turn_ctx(hand=['m1', 'm2'])
        rules = get_rule_set()
        assert validate_action(ctx, {'kind': 'discard', 'handIndex': 0}, rules)
        assert not validate_action(ctx, {'kind': 'discard', 'handIndex': 2}, rules)

    def test_win_rejected(self):
        ctx = turn_ctx()
        assert not validate_action(ctx, {'kind': 'win'}, get_rule_set())

    def test_chi_out_of_range(self):
        ctx = claim_ctx(chi_options=[{'tiles': ['m3', 'm4', 'm5'], 'kind': 'sequence'}])
        rules = get_rule_set('lotus-legacy')
        rules.round_state.joker_tiles = []
        assert validate_action(ctx, {'kind': 'chi', 'optionIndex': 0}, rules)
        assert not validate_action(ctx, {'kind': 'chi', 'optionIndex': 1}, rules)


# ── LLMPlayer（mock 供应商）──────────────────────────────────

class FakeResponse:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status
        self._payload = None

    def json(self):
        if self._payload is None:
            self._payload = {'choices': [{'message': {'content': self.text},
                                          'finish_reason': 'stop'}]}
        return self._payload


class FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    async def post(self, url, headers=None, json=None, timeout=None):
        self.calls += 1
        item = self._responses.pop(0) if self._responses else self._responses[-1]
        if callable(item):
            return item()
        return item


def make_llm_player(monkeypatch, responses, **cfg_overrides):
    cfg = dict(
        enabled=True, base_url='https://api.deepseek.com/v1', api_key='sk-x',
        model='deepseek-v4-flash', style='稳健', timeout_s=8.0,
        pool_timeout_s=1.0, concurrency=2, max_requests_per_room=0,
    )
    cfg.update(cfg_overrides)
    config = SimpleNamespace(**cfg)
    fake = FakeClient(responses)
    # LLMPlayer 直接使用 request_llm_decision —— mock 掉它（透传到假客户端）
    async def fake_decision(cfg_, system, user, candidate_ids):
        item = fake._responses.pop(0) if fake._responses else fake._responses[-1]
        if callable(item):
            item = item()
        if isinstance(item, Exception):
            raise item
        return parse_llm_output(item, candidate_ids)
    monkeypatch.setattr('app.game.llm_player.request_llm_decision', fake_decision)
    player = LLMPlayer(delays={'turn': 0, 'after_kong': 0, 'claim': 0}, config=config)
    return player, fake


class TestLLMPlayer:
    def test_turn_win_short_circuit_no_llm_call(self, monkeypatch):
        player, fake = make_llm_player(monkeypatch, [])
        # 白板癞子 + 4 面子 + 一对 → 胡
        ctx = turn_ctx(hand=['m1', 'm1', 'm1', 'm2', 'm2', 'm2', 'm3', 'm3', 'm3',
                             's1', 's1', 's1', 's2', 's2'])
        action = run(player.request_turn(ctx))
        assert action['kind'] == 'win'
        assert fake.calls == 0

    def test_turn_legal_choice_executes(self, monkeypatch):
        player, fake = make_llm_player(
            monkeypatch, ['{"choice":"A1","message":"稳一手。"}'])
        ctx = turn_ctx(hand=['m3', 'm3', 'm5', 'm6', 'p1', 'p2', 'p3', 's1', 's2', 's3',
                             'east', 'west', 'white'])
        action = run(player.request_turn(ctx))
        assert action['kind'] == 'discard'
        assert action['handIndex'] >= 0
        assert player.stats['successes'] == 1
        assert player.stats['messages'] == 1

    def test_turn_illegal_choice_falls_back(self, monkeypatch):
        # 返回白名单外的 choice → 解析失败 → 回退启发式（kind=discard 或杠）
        player, fake = make_llm_player(monkeypatch, [])
        ctx = turn_ctx(hand=['m3', 'm5', 'm6'])
        action = run(player.request_turn(ctx))
        assert action['kind'] == 'discard'
        assert player.stats['fallbacks'] >= 1

    def test_network_error_falls_back(self, monkeypatch):
        player, fake = make_llm_player(monkeypatch, [Exception('boom')])
        ctx = turn_ctx(hand=['m3', 'm5', 'm6'])
        action = run(player.request_turn(ctx))
        assert action['kind'] == 'discard'
        assert player.stats['fallbacks'] >= 1

    def test_claim_hu_short_circuit(self, monkeypatch):
        player, fake = make_llm_player(monkeypatch, [])
        ctx = claim_ctx(tile='m1', can_peng=True, can_hu=True)
        action = run(player.request_claim(ctx))
        assert action['kind'] == 'win'
        assert fake.calls == 0

    def test_claim_chi_mapping(self, monkeypatch):
        player, fake = make_llm_player(
            monkeypatch, ['{"choice":"C1","message":"吃！"}'])
        rules = get_rule_set('lotus-legacy')
        player.set_rule_set(rules)
        rules.round_state.joker_tiles = []
        ctx = claim_ctx(hand=['m4', 'm5', 'east'], tile='m3', can_peng=False,
                        chi_options=[{'tiles': ['m3', 'm4', 'm5'], 'kind': 'sequence'}])
        action = run(player.request_claim(ctx))
        assert action == {'kind': 'chi', 'optionIndex': 0}


# ── 房间装配（§9.3）───────────────────────────────────────────

class TestRoomAssembly:
    def test_llm_enabled_uses_llm_player(self, monkeypatch):
        from app.game.room import RoomSession
        monkeypatch.setattr('app.game.room.llm_server_available', lambda: True)
        room = RoomSession('LLMROOM', mode='east', capacity=2, llm_enabled=True)
        assert room.llm_available is True
        assert room.effective_llm_enabled is True
        # 四人桌固定 4 座；无真人占座时全部为 AI 补位
        controllers = room._controllers()
        assert sum(1 for c in controllers if isinstance(c, LLMPlayer)) == 4

    def test_llm_disabled_keeps_heuristic(self, monkeypatch):
        from app.game.player import AIPlayer
        from app.game.room import RoomSession
        monkeypatch.setattr('app.game.room.llm_server_available', lambda: True)
        room = RoomSession('HEURISTIC', mode='east', capacity=2, llm_enabled=False)
        assert room.effective_llm_enabled is False
        controllers = room._controllers()
        assert sum(1 for c in controllers if isinstance(c, AIPlayer)) == 4

    def test_server_unavailable_silently_degrades(self, monkeypatch):
        from app.game.player import AIPlayer
        from app.game.room import RoomSession
        monkeypatch.setattr('app.game.room.llm_server_available', lambda: False)
        room = RoomSession('OFF', mode='east', capacity=2, llm_enabled=True)
        assert room.llm_available is False
        assert room.effective_llm_enabled is False
        controllers = room._controllers()
        assert sum(1 for c in controllers if isinstance(c, AIPlayer)) == 4


# ── 每座位 LLM 配置 / 形象（联机空位自带配置）────────────────────

class TestPersona:
    def test_provider_folder_and_nickname(self):
        from app.llm.persona import avatar_url, default_nickname, display_name, provider_folder
        assert provider_folder('https://api.deepseek.com/v1') == 'deepseek'
        assert provider_folder('https://api.moonshot.cn/v1') == 'kimi'
        assert provider_folder('https://open.bigmodel.cn/api/paas/v4') == 'glm'
        assert provider_folder('https://my.proxy.local/v1') == 'custom'
        assert default_nickname('https://api.deepseek.com/v1') == '大肥鱼'
        assert default_nickname('https://x.com/v1', fallback='AI玩家') == 'AI玩家'
        assert avatar_url('https://api.deepseek.com/v1', '激进') == \
            'img/llm/deepseek/llm-avatar-jijin.png'
        assert display_name('大肥鱼', '激进') == '大肥鱼（激进）'


class TestSeatConfig:
    def test_build_valid_config(self):
        from app.llm.config import seat_config_from
        cfg = seat_config_from({
            'seat': 1, 'baseUrl': 'https://api.deepseek.com/v1', 'apiKey': 'sk-x',
            'model': 'deepseek-chat', 'style': '话痨', 'timeoutMs': 9000,
        })
        assert cfg is not None
        assert cfg.base_url == 'https://api.deepseek.com/v1'
        assert cfg.api_key == 'sk-x'
        assert cfg.style == '话痨'
        assert cfg.timeout_s == 9.0

    def test_invalid_entry_returns_none(self):
        from app.llm.config import seat_config_from
        assert seat_config_from({'seat': 1, 'baseUrl': 'https://x.com', 'model': 'm'}) is None
        assert seat_config_from({'seat': 2, 'baseUrl': '', 'apiKey': 'k', 'model': 'm'}) is None

    def test_style_and_timeout_normalized(self):
        from app.llm.config import seat_config_from
        cfg = seat_config_from({'baseUrl': 'https://x.com', 'apiKey': 'k', 'model': 'm',
                                'style': '狂暴', 'timeoutMs': 999999})
        assert cfg.style == '稳健'
        assert cfg.timeout_s == 120.0

    def test_valid_seat_entry_requires_normalizable_url_and_key(self):
        from app.llm.config import valid_seat_entry
        assert valid_seat_entry({'seat': 1, 'baseUrl': 'https://api.deepseek.com/v1',
                                 'apiKey': 'k', 'model': 'm'})
        assert not valid_seat_entry({'seat': 1, 'baseUrl': 'http://api.deepseek.com/v1',
                                     'apiKey': 'k', 'model': 'm'})
        assert not valid_seat_entry({'seat': 1, 'baseUrl': 'https://x.com', 'model': 'm'})


class TestPerSeatAssembly:
    def test_seat_configs_override_and_fallback(self, monkeypatch):
        from app.game.player import AIPlayer
        from app.game.room import RoomSession
        monkeypatch.setattr('app.game.room.llm_server_available', lambda: False)
        room = RoomSession('SEATS', mode='east', capacity=4, llm_enabled=True)
        room._llm_seat_configs = {
            1: {'seat': 1, 'baseUrl': 'https://api.deepseek.com/v1', 'apiKey': 'sk-d',
                'model': 'deepseek-chat', 'style': '激进', 'nickname': None, 'timeoutMs': None},
            3: {'seat': 3, 'baseUrl': 'https://api.moonshot.cn/v1', 'apiKey': 'sk-k',
                'model': 'kimi-k2', 'style': '稳健', 'nickname': '小K', 'timeoutMs': None},
        }
        # 服务端无全局配置：携带座配的座位用 LLMPlayer（各自 config），其余 AIPlayer
        controllers = room._controllers()
        assert isinstance(controllers[1], LLMPlayer)
        assert controllers[1].config.api_key == 'sk-d'
        assert controllers[1].config.style == '激进'
        assert isinstance(controllers[3], LLMPlayer)
        assert controllers[3].config.api_key == 'sk-k'
        assert isinstance(controllers[0], AIPlayer)
        assert isinstance(controllers[2], AIPlayer)

    def test_seed_display_name_and_avatar(self):
        from app.game.manager import PLAYER_SEED
        from app.game.room import RoomSession
        room = RoomSession('SEEDS', mode='east', capacity=4)
        room._llm_seat_configs = {
            2: {'seat': 2, 'baseUrl': 'https://api.deepseek.com/v1', 'apiKey': 'k',
                'model': 'm', 'style': '话痨', 'nickname': '', 'timeoutMs': None},
        }
        seeds = room._seeds()
        assert seeds[2]['name'] == '大肥鱼（话痨）'
        assert seeds[2]['avatar'] == 'img/llm/deepseek/llm-avatar-huayao.png'
        assert seeds[1]['name'] == PLAYER_SEED[1]['name']  # 未配置座位沿用 AI 种子

    def test_effective_with_seat_configs_without_server_config(self, monkeypatch):
        from app.game.room import RoomSession
        monkeypatch.setattr('app.game.room.llm_server_available', lambda: False)
        room = RoomSession('EF', mode='east', capacity=4, llm_enabled=True)
        assert room.effective_llm_enabled is False
        room._llm_seat_configs = {1: {'seat': 1, 'baseUrl': 'https://x.com', 'apiKey': 'k', 'model': 'm'}}
        assert room.effective_llm_enabled is True


# ── 局况元数据（§6.2/§6.4）───────────────────────────────────

class TestMeta:
    def test_llm_meta_fields(self):
        from app.game.manager import GameManager
        manager = GameManager()
        manager.players = [SimpleNamespace(score=1000, discards=[], melds=[],
                                           hand=['m1']) for _ in range(4)]
        manager.dealer = 1
        manager.round = 2
        manager.phase = 'drawing'
        manager._head_drawn = 4
        manager.wall = ['m2']
        meta = manager._llm_meta(2, 'turn')
        assert meta['seatWind'] == '南'   # 庄家 1 座 → 座位 2 为南
        assert meta['roundWind'] == '东'
        assert meta['dealerIndex'] == 1
        assert meta['roundIndex'] == 2
        assert meta['requestId'].startswith('turn-2-')
        assert 'drawing' in meta['stateVersion']
        assert meta['scores'] == [1000, 1000, 1000, 1000]
        assert len(meta['peers']) == 4
