"""安全的百度 TTS 连通性检查：不输出任何凭据或 Token。"""

import argparse
import asyncio

from dotenv import load_dotenv

from app.llm.config import default_provider_id
from app.tts.baidu import BaiduTtsClient
from app.tts.config import BACKEND_ROOT, TTS_STYLES, load_tts_config


async def _main(provider_id: str, style: str) -> None:
    # 独立检查命令不会经过 FastAPI/storage 启动链，须在这里显式加载 backend/.env。
    load_dotenv(BACKEND_ROOT / '.env', override=False)
    config = load_tts_config()
    print(f'TTS available={config.available}')
    if not config.available:
        return
    effective_provider = provider_id or default_provider_id() or ''
    voice = config.voice_for(style, effective_provider)
    print(
        f'TTS profile provider={effective_provider or "default"} style={style} '
        f'voice={voice.voice_id} speed={voice.speed} pitch={voice.pitch} volume={voice.volume}')
    client = BaiduTtsClient(config)
    try:
        audio = await client.synthesize('莲花麻将语音测试。', voice)
        print(f'TTS success bytes={len(audio)}')
    except Exception as exc:
        print(f'TTS failed {type(exc).__name__}: {exc}')
    finally:
        await client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='安全检查百度 TTS 配置与连通性')
    parser.add_argument('--provider', default='', help='LLM providerId，例如 deepseek')
    parser.add_argument('--style', choices=TTS_STYLES, default='稳健', help='AI 策略')
    args = parser.parse_args()
    asyncio.run(_main(args.provider, args.style))
