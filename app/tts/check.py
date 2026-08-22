"""安全的百度 TTS 连通性检查：不输出任何凭据或 Token。"""

import asyncio

from app.tts.baidu import BaiduTtsClient
from app.tts.config import load_tts_config


async def _main() -> None:
    config = load_tts_config()
    print(f'TTS available={config.available}')
    if not config.available:
        return
    client = BaiduTtsClient(config)
    try:
        audio = await client.synthesize('莲花麻将语音测试。', config.voices['稳健'])
        print(f'TTS success bytes={len(audio)}')
    except Exception as exc:
        print(f'TTS failed {type(exc).__name__}: {exc}')
    finally:
        await client.close()


if __name__ == '__main__':
    asyncio.run(_main())
