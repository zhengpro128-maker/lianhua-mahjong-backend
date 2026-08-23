"""LLM 后端接入单元测试 —— 文档 §13（无网络：mock 供应商调用）。

覆盖：规范/解析、候选与特征、合法性校验、LLMPlayer 三路径（成功/非法/异常回退、
胡短路、skipDraw 约束）、房间装配（llmEnabled/能力探测）、局况元数据。
"""

import asyncio
import json
import os
from types import SimpleNamespace

import httpx
import pytest

from app.game.llm_player import LLMPlayer
from app.game.player import ClaimContext, TurnContext
from app.llm.candidates import build_request
from app.llm.client import extract_json_object, parse_llm_output
from app.llm.prompt import build_prompt
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

    def test_classic_white_joker_is_not_discard_candidate_while_natural_tiles_exist(self):
        ctx = turn_ctx(hand=['white', 'm1', 'm2'])
        built = build_request(ctx, get_rule_set(), 'r1', 'v1', 'turn')
        discards = [c for c in built['request']['candidates']
                    if c['action']['kind'] == 'discard']
        assert [c['label'] for c in discards] == ['出1万', '出2万']
        assert built['request']['state']['jokerTiles'] == ['白板']

    def test_lotus_double_jokers_and_white_are_protected(self):
        rules = get_rule_set('lotus-legacy')
        rules.round_state.joker_tiles = ['m5', 'm6']
        ctx = turn_ctx(hand=['m5', 'm6', 'white', 'm1'], jokers=['m5', 'm6'])
        built = build_request(ctx, rules, 'r1', 'v1', 'turn')
        discards = [c for c in built['request']['candidates']
                    if c['action']['kind'] == 'discard']
        assert [c['label'] for c in discards] == ['出1万']

    def test_all_wildcards_still_produce_discard_candidates_with_warning(self):
        rules = get_rule_set('lotus-legacy')
        rules.round_state.joker_tiles = ['m5', 'm6']
        ctx = turn_ctx(hand=['m5', 'm6', 'white'], jokers=['m5', 'm6'])
        built = build_request(ctx, rules, 'r1', 'v1', 'turn')
        discards = [c for c in built['request']['candidates']
                    if c['action']['kind'] == 'discard']
        assert [c['label'] for c in discards] == ['出5万', '出6万', '出白板']
        assert all(any('癞子/精牌' in risk for risk in c['features']['risks'])
                   for c in discards)


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

    def test_rejects_joker_discard_when_natural_tile_exists(self):
        classic = get_rule_set()
        classic_ctx = turn_ctx(hand=['white', 'm1'], jokers=['white'])
        assert not validate_action(classic_ctx, {'kind': 'discard', 'handIndex': 0}, classic)
        assert validate_action(classic_ctx, {'kind': 'discard', 'handIndex': 1}, classic)

        lotus = get_rule_set('lotus-legacy')
        lotus.round_state.joker_tiles = ['m5', 'm6']
        lotus_ctx = turn_ctx(hand=['m5', 'white', 'm1'], jokers=['m5', 'm6'])
        assert not validate_action(lotus_ctx, {'kind': 'discard', 'handIndex': 0}, lotus)
        assert not validate_action(lotus_ctx, {'kind': 'discard', 'handIndex': 1}, lotus)
        assert validate_action(lotus_ctx, {'kind': 'discard', 'handIndex': 2}, lotus)

    def test_all_wildcards_can_discard_to_avoid_empty_action_set(self):
        rules = get_rule_set('lotus-legacy')
        rules.round_state.joker_tiles = ['m5', 'm6']
        ctx = turn_ctx(hand=['m5', 'white'], jokers=['m5', 'm6'])
        assert validate_action(ctx, {'kind': 'discard', 'handIndex': 0}, rules)


class TestPromptRules:
    def test_classic_prompt_marks_white_and_forbids_other_variant_patterns(self):
        built = build_request(turn_ctx(hand=['white', 'm1', 'm2']), get_rule_set(),
                              'r1', 'v1', 'turn')
        system, user = build_prompt('稳健', built['request'])
        assert '规则摘要未列出的特殊牌型一律视为不支持' in system
        assert '不支持七对、十三幺、十三烂、七星十三烂' in user
        assert '【癞子规则】白板是本玩法的万能牌' in user
        assert '出白板' not in user

    def test_lotus_prompt_uses_double_joker_and_limited_white_rule(self):
        rules = get_rule_set('lotus-legacy')
        rules.round_state.joker_tiles = ['m5', 'm6']
        ctx = turn_ctx(hand=['m5', 'm6', 'white', 'm1'], jokers=['m5', 'm6'])
        built = build_request(ctx, rules, 'r1', 'v1', 'turn')
        _, user = build_prompt('稳健', built['request'])
        assert '翻出的牌面及其同序下一张均为精牌' in user
        assert '精牌「5万、6万」可代任意牌' in user
        assert '白板只能替代上述精牌面或白板本身' in user
        assert '出5万' not in user
        assert '出6万' not in user
        assert '出白板' not in user


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


def make_llm_player(monkeypatch, responses, *, seat=-1, provider_id='', on_message=None,
                    **cfg_overrides):
    cfg = dict(
        enabled=True, base_url='https://api.deepseek.com/v1', api_key='sk-x',
        model='deepseek-v4-flash', style='稳健', timeout_s=20.0,
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
    player = LLMPlayer(
        delays={'turn': 0, 'after_kong': 0, 'claim': 0},
        config=config,
        seat=seat,
        provider_id=provider_id,
        on_message=on_message,
    )
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
        messages = []
        player, fake = make_llm_player(
            monkeypatch, ['{"choice":"A1","message":"稳一手。"}'],
            seat=2, provider_id='deepseek',
            on_message=lambda seat, text, priority: messages.append((seat, text, priority)))
        ctx = turn_ctx(hand=['m3', 'm3', 'm5', 'm6', 'p1', 'p2', 'p3', 's1', 's2', 's3',
                             'east', 'west', 'white'])
        action = run(player.request_turn(ctx))
        assert action['kind'] == 'discard'
        assert action['handIndex'] >= 0
        assert player.stats['successes'] == 1
        assert player.stats['messages'] == 1
        assert player.message_history == ['稳一手。']
        assert messages == [(2, '稳一手。', 'normal')]

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
        monkeypatch.setattr('app.game.room.load_llm_providers', lambda: {
            'ds': deepseek_provider()})
        monkeypatch.setattr('app.game.room.default_provider_id', lambda: 'ds')
        room = RoomSession('LLMROOM', mode='east', capacity=2, llm_enabled=True)
        assert room.llm_available is True
        assert room.effective_llm_enabled is True
        # 四人桌固定 4 座；无真人占座时全部为 LLM 补位（默认提供商）
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
        monkeypatch.setattr('app.game.room.load_llm_providers', lambda: {})
        room = RoomSession('OFF', mode='east', capacity=2, llm_enabled=True)
        assert room.llm_available is False
        assert room.effective_llm_enabled is False
        controllers = room._controllers()
        assert sum(1 for c in controllers if isinstance(c, AIPlayer)) == 4


# ── 服务端多提供商 / 形象（§9.7）────────────────────────────────

def deepseek_provider(style='稳健', nickname=''):
    """测试用 DeepSeek 提供商（key 存服务端；客户端只引用 id）。"""
    from app.llm.config import LlmProvider
    return LlmProvider('deepseek', 'DeepSeek', 'https://api.deepseek.com/v1',
                       'sk-server-ds', 'deepseek-chat', style, nickname)


def kimi_provider(style='稳健', nickname='小K'):
    from app.llm.config import LlmProvider
    return LlmProvider('kimi', 'Kimi', 'https://api.moonshot.cn/v1',
                       'sk-server-kimi', 'kimi-k2', style, nickname)


class TestPersona:
    def test_provider_folder_and_nickname(self):
        from app.llm.persona import avatar_url, default_nickname, display_name, provider_folder
        assert provider_folder('https://api.deepseek.com/v1') == 'deepseek'
        assert provider_folder('https://api.moonshot.cn/v1') == 'kimi'
        assert provider_folder('https://open.bigmodel.cn/api/paas/v4') == 'glm'
        assert provider_folder('https://my.proxy.local/v1') == 'custom'
        assert provider_folder('https://my.proxy.local/v1', provider_id='relay_gpt') == 'gpt'
        assert provider_folder('https://my.proxy.local/v1', avatar_folder='claude',
                               provider_id='relay_gpt') == 'claude'
        assert default_nickname('https://api.deepseek.com/v1') == '大肥鱼'
        assert default_nickname('https://x.com/v1', fallback='AI玩家') == 'AI玩家'
        assert default_nickname('https://x.com/v1', provider_id='relay_gpt') == 'GPT'
        assert avatar_url('https://api.deepseek.com/v1', '激进') == \
            'img/llm/deepseek/llm-avatar-jijin.png'
        assert avatar_url('https://my.proxy.local/v1', '稳健',
                          provider_id='relay_gpt') == \
            'img/llm/gpt/llm-avatar-wenjian.png'
        assert display_name('大肥鱼', '激进') == '大肥鱼（激进）'


class TestProviderRegistry:
    def test_env_registry_parsing(self, monkeypatch):
        # 与开发机 backend/.env 隔离，只验证本用例声明的注册表。
        for key in list(os.environ):
            if key.startswith('LLM_PROVIDER_'):
                monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv('LLM_PROVIDER_DEEPSEEK_BASE_URL', 'https://api.deepseek.com/v1')
        monkeypatch.setenv('LLM_PROVIDER_DEEPSEEK_API_KEY', 'sk-srv')
        monkeypatch.setenv('LLM_PROVIDER_DEEPSEEK_MODEL', 'deepseek-chat')
        monkeypatch.setenv('LLM_PROVIDER_KIMI_BASE_URL', 'https://api.moonshot.cn/v1')
        monkeypatch.setenv('LLM_PROVIDER_KIMI_API_KEY', 'sk-k')
        monkeypatch.setenv('LLM_PROVIDER_KIMI_MODEL', 'kimi-k2')
        monkeypatch.setenv('LLM_PROVIDER_KIMI_STYLE', '话痨')
        monkeypatch.setenv('LLM_PROVIDER_KIMI_AVATAR_FOLDER', 'kimi')
        from app.llm.config import load_llm_providers
        providers = load_llm_providers()
        assert set(providers) == {'deepseek', 'kimi'}
        assert providers['kimi'].style == '话痨'
        assert providers['kimi'].name == 'kimi'
        assert providers['kimi'].avatar_folder == 'kimi'

    def test_incomplete_provider_skipped(self, monkeypatch):
        monkeypatch.delenv('LLM_PROVIDER_X_API_KEY', raising=False)
        monkeypatch.setenv('LLM_PROVIDER_X_BASE_URL', 'https://x.com/v1')
        monkeypatch.setenv('LLM_PROVIDER_X_MODEL', 'm')  # 缺 key
        from app.llm.config import load_llm_providers
        assert 'x' not in load_llm_providers()

    def test_legacy_global_fallback_as_default(self, monkeypatch):
        from app.llm.config import load_llm_providers
        monkeypatch.setenv('LLM_ENABLED', 'true')
        monkeypatch.setenv('LLM_API_BASE', 'https://api.deepseek.com/v1')
        monkeypatch.setenv('LLM_API_KEY', 'sk-legacy')
        monkeypatch.setenv('LLM_MODEL', 'deepseek-chat')
        for key in list(os.environ):
            if key.startswith('LLM_PROVIDER_'):
                monkeypatch.delenv(key, raising=False)
        providers = load_llm_providers()
        assert 'default' in providers
        assert providers['default'].api_key == 'sk-legacy'

    def test_default_provider_id(self, monkeypatch):
        from app.llm.config import default_provider_id
        monkeypatch.setattr('app.llm.config.load_llm_providers', lambda: {
            'kimi': kimi_provider(), 'deepseek': deepseek_provider()})
        assert default_provider_id() == 'kimi'

    def test_to_config_limits(self):
        cfg = deepseek_provider(style='狂暴', nickname='').to_config()
        assert cfg.style == '稳健'
        assert cfg.api_key == 'sk-server-ds'
        assert cfg.timeout_s == 20.0
        assert deepseek_provider(style='稳健').to_config(style_override='高冷').style == '高冷'

    def test_qwen_thinking_model_uses_fast_default_timeout(self):
        from app.llm.config import LlmProvider, is_qwen_thinking_model
        provider = LlmProvider(
            'qwen', base_url='https://dashscope.aliyuncs.com/compatible-mode/v1',
            api_key='sk-qwen', model='qwen3.7-plus')
        assert is_qwen_thinking_model(provider.base_url, provider.model)
        assert provider.to_config().timeout_s == 8.0
        provider.timeout_ms = 12_000
        assert provider.to_config().timeout_s == 12.0

    def test_qwen_request_disables_thinking_and_requests_json(self, monkeypatch):
        from app.llm.client import request_llm_decision
        from app.llm.config import LlmServerConfig

        captured = {}

        async def handler(request: httpx.Request):
            captured.update(json.loads(request.content))
            return httpx.Response(200, json={
                'choices': [{
                    'message': {'content': '{"choice":"A1","message":"稳住"}'},
                    'finish_reason': 'stop',
                }],
                'usage': {
                    'completion_tokens': 6,
                    'completion_tokens_details': {'reasoning_tokens': 0},
                },
            })

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        monkeypatch.setattr('app.llm.client.get_llm_client', lambda: http)
        cfg = LlmServerConfig(
            enabled=True,
            base_url='https://dashscope.aliyuncs.com/compatible-mode/v1',
            api_key='sk-qwen', model='qwen3.7-plus', timeout_s=8,
            provider_id='qwen')
        assert run(request_llm_decision(cfg, 'system', 'user', ['A1'])) == ('A1', '稳住')
        assert captured['enable_thinking'] is False
        assert captured['response_format'] == {'type': 'json_object'}
        run(http.aclose())


class TestPerSeatAssembly:
    def test_room_broadcasts_messages_and_logs_match_summary(self, monkeypatch):
        from app.game.player import AIPlayer
        from app.game.room import RoomSession

        emitted = []
        logs = []
        room = RoomSession('LOGS', mode='east', capacity=4, llm_enabled=True)
        monkeypatch.setattr(room.conn, 'broadcast', lambda message: emitted.append(message))
        controller = LLMPlayer(
            config=deepseek_provider(style='话痨').to_config(),
            seat=1,
            provider_id='deepseek',
        )
        controller.stats.update({
            'requests': 3, 'successes': 2, 'fallbacks': 1,
            'messages': 2, 'invalid': 0,
        })
        room.manager = SimpleNamespace(controllers=[AIPlayer(), controller, AIPlayer(), AIPlayer()])
        room._on_llm_message(1, '稳住，先打这张。')
        assert emitted == [{
            'kind': 'llm_message', 'id': 1, 'seat': 1, 'text': '稳住，先打这张。', 'priority': 'normal',
        }]

        class BoundLogger:
            def __init__(self, context):
                self.context = context

            def info(self, message):
                logs.append((self.context, message))

        monkeypatch.setattr(
            'app.game.room.logger',
            SimpleNamespace(bind=lambda **context: BoundLogger(context)),
        )
        room._log_llm_match_summary()
        assert any('LLM 场次统计 请求=3 成功=2 回退=1 吐槽=2' in message
                   for _, message in logs)
        assert any(message == 'LLM 吐槽：稳住，先打这张。' for _, message in logs)

    def test_room_broadcasts_tts_audio_after_message(self, monkeypatch):
        from app.game.player import AIPlayer
        from app.game.room import RoomSession

        emitted = []
        room = RoomSession('AUDIO', mode='east', capacity=4, llm_enabled=True)
        monkeypatch.setattr(room.conn, 'broadcast', lambda message: emitted.append(message))
        controller = LLMPlayer(
            config=deepseek_provider(style='高冷').to_config(),
            seat=1,
            provider_id='deepseek',
        )
        room.manager = SimpleNamespace(controllers=[AIPlayer(), controller, AIPlayer(), AIPlayer()])

        class FakeTtsService:
            available = True

            async def ensure_audio(self, text, style, provider_id):
                assert (text, style, provider_id) == ('这张先放下。', '高冷', 'deepseek')
                return SimpleNamespace(
                    audio_url=f'/api/tts/audio/{"a" * 64}.mp3', cached=True)

        monkeypatch.setattr('app.game.room.get_tts_service', lambda: FakeTtsService())

        async def scenario():
            room._on_llm_message(1, '这张先放下。')
            await asyncio.gather(*list(room._tts_tasks))

        run(scenario())
        assert emitted[0] == {
            'kind': 'llm_message', 'id': 1, 'seat': 1, 'text': '这张先放下。', 'priority': 'normal',
        }
        assert emitted[1] == {
            'kind': 'llm_audio', 'messageId': 1, 'seat': 1,
            'audioUrl': f'/api/tts/audio/{"a" * 64}.mp3', 'cached': True, 'priority': 'normal',
        }
        assert room._tts_match_stats['hits'] == 1

    @pytest.mark.parametrize(
        ('action_type', 'expected_text'),
        [
            ('self-draw', '自摸，水到渠成。'),
            ('discard-win', '放枪，这张正合适。'),
        ],
    )
    def test_llm_winner_uses_message_tts_path_and_is_marked_in_snapshot(
            self, action_type, expected_text):
        from app.game.manager import GameManager
        from app.game.player import AIPlayer
        from app.game.room import RoomSession, WSEvents, build_snapshot
        from app.models.game import GamePlayer

        emitted = []
        room = RoomSession('WINVOICE', mode='east', capacity=4, llm_enabled=True)
        room.conn.broadcast = lambda message: emitted.append(message)
        controller = LLMPlayer(
            config=deepseek_provider(style='稳健').to_config(),
            seat=1,
            provider_id='deepseek',
        )
        controllers = [AIPlayer(), controller, AIPlayer(), AIPlayer()]
        room.manager = GameManager(controllers=controllers)
        room.manager.players = [
            GamePlayer(
                name=f'P{seat}', avatar='', score=1000, seat=seat,
                hand=[], discards=[], melds=[], redCount=0, drawnTileIndex=-1,
            )
            for seat in range(4)
        ]

        WSEvents(room).show_table_action(action_type, 1, 0, 'm1', -1)

        assert emitted[0]['kind'] == 'table_action'
        assert emitted[1] == {
            'kind': 'llm_message', 'id': 1, 'seat': 1, 'text': expected_text, 'priority': 'important',
        }
        snapshot = build_snapshot(room, 0)
        assert [player['isLlm'] for player in snapshot['players']] == [False, True, False, False]

    def test_llm_win_lines_have_three_short_variants_per_style(self):
        from app.game.room import _LLM_WIN_LINES
        for styles in _LLM_WIN_LINES.values():
            for variants in styles.values():
                assert len(variants) == 3
                assert len(set(variants)) == 3
                assert all(len(line) <= 16 for line in variants)

    def test_seat_provider_ids_resolve_per_seat(self, monkeypatch):
        from app.game.player import AIPlayer
        from app.game.room import RoomSession
        monkeypatch.setattr('app.game.room.llm_server_available', lambda: True)
        monkeypatch.setattr('app.game.room.load_llm_providers', lambda: {
            'ds': deepseek_provider(style='激进'), 'kimi': kimi_provider()})
        room = RoomSession('SEATS', mode='east', capacity=4, llm_enabled=True)
        room._llm_seat_providers = {1: 'ds', 3: 'kimi'}
        room._llm_seat_styles = {1: '高冷', 3: '话痨'}
        room._llm_default_provider = 'ds'
        controllers = room._controllers()
        assert isinstance(controllers[1], LLMPlayer)
        assert controllers[1].config.api_key == 'sk-server-ds'
        assert controllers[1].config.style == '高冷'
        assert isinstance(controllers[3], LLMPlayer)
        assert controllers[3].config.api_key == 'sk-server-kimi'
        assert controllers[3].config.style == '话痨'
        # 未指定座位 → 默认提供商 ds
        assert isinstance(controllers[2], LLMPlayer)
        assert controllers[2].config.api_key == 'sk-server-ds'
        assert isinstance(controllers[0], AIPlayer)

    def test_seed_display_name_and_avatar(self, monkeypatch):
        from app.game.manager import PLAYER_SEED
        from app.game.player import AIPlayer
        from app.game.room import RoomSession
        monkeypatch.setattr('app.game.room.llm_server_available', lambda: True)
        monkeypatch.setattr('app.game.room.load_llm_providers', lambda: {
            'ds': deepseek_provider(style='话痨'), 'kimi': kimi_provider()})
        room = RoomSession('SEEDS', mode='east', capacity=4, llm_enabled=True)
        room._llm_seat_providers = {2: 'ds'}
        room._llm_seat_styles = {2: '激进'}
        room._llm_default_provider = 'kimi'
        seeds = room._seeds()
        assert seeds[2]['name'] == '大肥鱼（激进）'
        assert seeds[2]['avatar'] == 'img/llm/deepseek/llm-avatar-jijin.png'
        # 未指定座位 → 默认提供商 kimi
        assert seeds[1]['name'] == '小K（稳健）'
        assert seeds[1]['avatar'] == 'img/llm/kimi/llm-avatar-wenjian.png'
        # 无 LLM 能力时沿用 AI 种子
        monkeypatch.setattr('app.game.room.llm_server_available', lambda: False)
        room2 = RoomSession('SEEDS2', mode='east', capacity=4, llm_enabled=True)
        assert room2._seeds()[1]['name'] == PLAYER_SEED[1]['name']

    def test_unknown_provider_falls_back_to_heuristic(self, monkeypatch):
        from app.game.player import AIPlayer
        from app.game.room import RoomSession
        monkeypatch.setattr('app.game.room.llm_server_available', lambda: True)
        monkeypatch.setattr('app.game.room.load_llm_providers', lambda: {'ds': deepseek_provider()})
        room = RoomSession('UNK', mode='east', capacity=4, llm_enabled=True)
        room._llm_seat_providers = {1: 'ghost'}  # 未知 id（开局校验已拦，此处兜底）
        room._llm_default_provider = 'ds'
        controllers = room._controllers()
        assert isinstance(controllers[1], AIPlayer)
        assert isinstance(controllers[2], LLMPlayer)


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
