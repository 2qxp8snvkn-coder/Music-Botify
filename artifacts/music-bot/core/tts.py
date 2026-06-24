import os
import hashlib
import time
import asyncio
import io
import aiohttp

try:
    import edge_tts
    EDGE_TTS_OK = True
except ImportError:
    EDGE_TTS_OK = False

from lavalink.server import LoadType
from core.voice import LavalinkVoiceClient
from core.logger import logger

MAX_TEXT  = 400
CACHE_TTL = 1800
CACHE_MAX = 500

HINDI_VOICES: dict[str, tuple[str, str]] = {
    "swara":  ("hi-IN-SwaraNeural",  "♀"),
    "madhur": ("hi-IN-MadhurNeural", "♂"),
}

DEFAULT_VOICE = "swara"


class TTSManager:
    def __init__(self, player_manager):
        self.pm         = player_manager
        self.client     = player_manager.client
        self._voice:      dict[int, str]                = {}
        self._url_cache:  dict[str, tuple[str, float]]  = {}
        self._locks:      dict[int, asyncio.Lock]       = {}
        self._tts_conns:  set[int]                      = set()
        self._session:    aiohttp.ClientSession | None   = None



    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    def _lock(self, gid: int) -> asyncio.Lock:
        if gid not in self._locks:
            self._locks[gid] = asyncio.Lock()
        return self._locks[gid]

    def get_voice_key(self, gid: int) -> str:
        k = self._voice.get(gid, DEFAULT_VOICE)
        return k if k in HINDI_VOICES else DEFAULT_VOICE

    def get_voice_id(self, gid: int) -> str:
        return HINDI_VOICES[self.get_voice_key(gid)][0]

    def set_voice(self, gid: int, key: str) -> bool:
        if key not in HINDI_VOICES:
            return False
        self._voice[gid] = key
        return True



    def _cached_url(self, text: str, vid: str) -> str | None:
        k = hashlib.md5(f"{text}:{vid}".encode()).hexdigest()
        e = self._url_cache.get(k)
        return e[0] if e and (time.monotonic() - e[1]) < CACHE_TTL else None

    def _store_url(self, text: str, vid: str, url: str):
        if len(self._url_cache) >= CACHE_MAX:
            del self._url_cache[min(self._url_cache, key=lambda k: self._url_cache[k][1])]
        self._url_cache[hashlib.md5(f"{text}:{vid}".encode()).hexdigest()] = (url, time.monotonic())



    async def _generate_bytes(self, text: str, vid: str) -> bytes | None:
        buf = io.BytesIO()
        try:
            async for chunk in edge_tts.Communicate(text, vid).stream():
                if chunk["type"] == "audio":
                    buf.write(chunk["data"])
            data = buf.getvalue()
            return data if data else None
        except Exception as e:
            logger.error(f"[TTS] edge-tts error: {e}")
            return None

    async def _upload_bytes(self, data: bytes) -> str | None:
        try:
            session = await self._get_session()
            form = aiohttp.FormData()
            form.add_field('reqtype', 'fileupload')
            form.add_field('time', '1h')
            form.add_field('fileToUpload', data, filename='tts.mp3', content_type='audio/mpeg')
            async with session.post(
                'https://litterbox.catbox.moe/resources/internals/api.php',
                data=form, timeout=aiohttp.ClientTimeout(total=15)
            ) as r:
                if r.status == 200:
                    raw = (await r.text()).strip()
                    return raw if raw.startswith('http') else None
        except Exception as e:
            logger.error(f"[TTS] Upload error: {e}")
        return None

    async def _resolve(self, text: str, vid: str) -> str | None:
        cached = self._cached_url(text, vid)
        if cached:
            return cached
        data = await self._generate_bytes(text, vid)
        if data is None:
            return None
        url = await self._upload_bytes(data)
        if url:
            self._store_url(text, vid, url)
        return url



    async def _connect(self, guild, channel) -> bool:
        try:
            await channel.connect(cls=LavalinkVoiceClient)
            self._tts_conns.add(guild.id)
            return True
        except Exception as e:
            logger.error(f"[TTS] Connect error: {e}")
            return False

    async def _queue(self, gid: int, url: str) -> bool:
        player = self.client.lavalink.player_manager.get(gid)
        if player is None:
            player = self.client.lavalink.player_manager.create(gid)

        tts_cnt = player.fetch('tts_count') or 0
        if not player.is_playing and tts_cnt == 0:
            player.queue.clear()

        res = None
        for attempt in range(3):
            try:
                if player.node:
                    res = await player.node.get_tracks(url)
                else:
                    res = await self.client.lavalink.get_tracks(url)
                if res and res.load_type not in (LoadType.EMPTY, LoadType.ERROR) and res.tracks:
                    break
                res = None
            except Exception:
                pass
            if attempt < 2:
                await asyncio.sleep(0.5)

        if not res or not res.tracks:
            return False

        track = res.tracks[0]
        track.extra['tts'] = True
        player.add(track=track, requester=self.client.user.id)
        player.store('tts_count', tts_cnt + 1)

        if not player.is_playing:
            await player.play()
        return True



    async def speak(self, guild_id: int, voice_channel_id: int, text: str):

        if not EDGE_TTS_OK:
            return logger.error("[TTS] edge-tts is not installed. Run: pip install edge-tts")

        if len(text) > MAX_TEXT:
            return logger.error(f"[TTS] Text too long — max {MAX_TEXT} chars, you sent {len(text)}.")

        guild = self.client.get_guild(guild_id)
        if not guild:
            return logger.error("[TTS] Guild not found.")

        voice_channel = guild.get_channel(voice_channel_id)
        if not voice_channel:
            return logger.error("[TTS] Voice channel not found.")


        player = self.client.lavalink.player_manager.get(guild_id) if hasattr(self.client, 'lavalink') else None
        cnt = (player.fetch('tts_count') or 0) if player else 0
        if player and player.is_playing and cnt == 0:
            return logger.error("[TTS] Music is currently playing. Stop it first.")


        vc = guild.voice_client
        if vc is None:
            if not await self._connect(guild, voice_channel):
                return logger.error("[TTS] Failed to join voice channel.")

        vkey = self.get_voice_key(guild_id)
        vid  = HINDI_VOICES[vkey][0]
        gender = HINDI_VOICES[vkey][1]

        async with self._lock(guild_id):
            logger.info(f"[TTS] Generating audio ({gender} {vkey.capitalize()})...")
            url = await self._resolve(text, vid)
            if url is None:
                return logger.error("[TTS] Failed to generate TTS audio.")
            ok = await self._queue(guild_id, url)

        if not ok:
            return logger.error("[TTS] Failed to load audio into player.")

        preview = f'"{text[:80]}{"..." if len(text) > 80 else ""}"'
        logger.success(f"[TTS] Speaking: {preview}", voice=f"{gender} {vkey.capitalize()}")

    async def resolve_url(self, guild_id: int, text: str) -> str | None:
        if not EDGE_TTS_OK:
            logger.error("[TTS] edge-tts is not installed. Run: pip install edge-tts")
            return None
        if len(text) > MAX_TEXT:
            logger.error(f"[TTS] Text too long — max {MAX_TEXT} chars, you sent {len(text)}.")
            return None
        vkey = self.get_voice_key(guild_id)
        vid = HINDI_VOICES[vkey][0]
        gender = HINDI_VOICES[vkey][1]
        logger.info(f"[TTS] Generating audio ({gender} {vkey.capitalize()})...")
        url = await self._resolve(text, vid)
        if url is None:
            logger.error("[TTS] Failed to generate TTS audio.")
        return url

    async def play_url(self, guild_id: int, voice_channel_id: int, url: str):
        guild = self.client.get_guild(guild_id)
        if not guild:
            return logger.error("[TTS] Guild not found.")
        voice_channel = guild.get_channel(voice_channel_id)
        if not voice_channel:
            return logger.error("[TTS] Voice channel not found.")
        player = self.client.lavalink.player_manager.get(guild_id) if hasattr(self.client, 'lavalink') else None
        cnt = (player.fetch('tts_count') or 0) if player else 0
        if player and player.is_playing and cnt == 0:
            return logger.error("[TTS] Music is currently playing. Stop it first.")
        vc = guild.voice_client
        if vc is None:
            if not await self._connect(guild, voice_channel):
                return logger.error("[TTS] Failed to join voice channel.")
        async with self._lock(guild_id):
            ok = await self._queue(guild_id, url)
        if not ok:
            return logger.error("[TTS] Failed to load audio into player.")
        logger.success("[TTS] Queued TTS audio.")

    async def stop(self, guild_id: int):

        guild = self.client.get_guild(guild_id)
        if not guild:
            return logger.error("[TTS] Guild not found.")

        player = self.client.lavalink.player_manager.get(guild_id) if hasattr(self.client, 'lavalink') else None
        vc = guild.voice_client

        if vc is None and player is None:
            return logger.error("[TTS] Not connected to any voice channel.")

        if player:
            await player.stop()
            player.queue.clear()
            player.store('tts_count', 0)
        if vc:
            await vc.disconnect(force=True)

        self._tts_conns.discard(guild_id)
        logger.success("[TTS] Stopped and disconnected.")

    def list_voices(self):

        logger.separator()
        from colorama import Fore, Style
        from pystyle import Colorate
        from core.utils import PRIMARY
        print(Colorate.Horizontal(PRIMARY, "  HINDI TTS VOICES".center(40)))
        logger.separator()
        current_default = DEFAULT_VOICE
        for key, (vid, gender) in HINDI_VOICES.items():
            marker = f"{Fore.GREEN}(default){Style.RESET_ALL}" if key == current_default else ""
            print(f"  {Fore.CYAN}*{Style.RESET_ALL} {gender} {key.capitalize()} {Fore.LIGHTBLACK_EX}({vid}){Style.RESET_ALL} {marker}")
        logger.separator()

    def show_voice(self, guild_id: int):

        vkey = self.get_voice_key(guild_id)
        gender = HINDI_VOICES[vkey][1]
        logger.info(f"[TTS] Current voice: {gender} {vkey.capitalize()}")
