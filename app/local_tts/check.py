"""安全检查独立单机 TTS 网关；不输出任何凭据。"""

import argparse
import asyncio

from dotenv import load_dotenv

from app.local_tts.service import LocalTtsGatewayService
from app.tts.config import BACKEND_ROOT, TTS_STYLES


async def _main(voice_key: str, style: str) -> None:
    load_dotenv(BACKEND_ROOT / '.env', override=False)
    service = LocalTtsGatewayService()
    try:
        print(f'Local TTS available={service.available}')
        print(f'Local TTS profile voiceKey={voice_key} style={style}')
        audio = await service.ensure_audio('单机语音链路测试。', voice_key, style)
        if audio is None:
            print('Local TTS failed')
            return
        print(
            f'Local TTS success cacheKey={audio.cache_key} '
            f'bytes={audio.size_bytes} cached={audio.cached}')
    finally:
        await service.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='检查独立单机 TTS 网关')
    parser.add_argument('--voice-key', default='deepseek')
    parser.add_argument('--style', choices=TTS_STYLES, default='稳健')
    args = parser.parse_args()
    asyncio.run(_main(args.voice_key, args.style))
