import re
import random
import asyncio
import aiohttp
import discord
import lavalink
from lavalink.server import LoadType
from core.voice import LavalinkVoiceClient
from core.filters import apply_filter, clear_filters, list_filters
from core.logger import logger
from core.utils import format_time
from core.tts import TTSManager

MAX_SEARCH_RETRIES = 2
AUTO_SKIP_DELAY = 1.5

url_rx = re.compile(r"https?://(?:www\.)?.+")

SOURCE_PREFIXES = {
    "sp ": "spsearch",
    "yt ": "ytsearch",
    "sc ": "scsearch",
    "js ": "jssearch",
    "am ": "amsearch",
    "dz ": "dzsearch",
}

SOURCE_NAMES = {
    "spsearch": "Spotify",
    "ytsearch": "YouTube",
    "scsearch": "SoundCloud",
    "jssearch": "JioSaavn",
    "amsearch": "Apple Music",
    "dzsearch": "Deezer",
}

SOURCE_EMOJI = {
    "Spotify":     "🟢",
    "YouTube":     "🔴",
    "SoundCloud":  "🟠",
    "JioSaavn":    "🔵",
    "Apple Music": "🍎",
    "Deezer":      "🟣",
    "Best Match":  "🔍",
    "URL":         "🔗",
}

def _source_badge(label: str) -> str:
    emoji = SOURCE_EMOJI.get(label, "🎵")
    return f"{emoji} {label}"

URL_PATTERNS = [
    (re.compile(r"https?://(open\.spotify\.com|spotify\.com)/"), "Spotify"),
    (re.compile(r"https?://(www\.youtube\.com|youtu\.be|music\.youtube\.com)/"), "YouTube"),
    (re.compile(r"https?://music\.apple\.com/"), "Apple Music"),
    (re.compile(r"https?://(www\.)?jiosaavn\.com/"), "JioSaavn"),
    (re.compile(r"https?://(www\.)?soundcloud\.com/"), "SoundCloud"),
    (re.compile(r"https?://(www\.)?deezer\.com/"), "Deezer"),
]

MULTI_SEARCH_SOURCES = ["ytsearch", "spsearch", "scsearch", "jssearch"]


def _score_result(query, track, source_key):
    query_lower = query.lower()
    title_lower = track.title.lower()
    author_lower = track.author.lower() if track.author else ""
    score = 0
    if query_lower in title_lower:
        score += 50
    if query_lower == title_lower:
        score += 100
    query_words = set(query_lower.split())
    title_words = set(title_lower.split())
    author_words = set(author_lower.split())
    score += len(query_words & title_words) * 20
    score += len(query_words & author_words) * 15
    if track.duration > 0:
        score += 10
    if source_key == "spsearch":
        score += 8
    elif source_key == "ytsearch":
        score += 5
    return score


class PlayerManager:
    def __init__(self, bot, config, guild_id=None):
        self.bot = bot
        self.config = config
        self.guild_id = guild_id
        self.active_filter = {}
        self._http_session = None
        self.tts = TTSManager(self)

    @property
    def client(self):
        return self.bot

    async def _get_session(self):
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession()
        return self._http_session

    async def close(self):
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
            self._http_session = None

    def setup_lavalink(self):
        if not hasattr(self.bot, 'lavalink') or self.bot.lavalink is None:
            self.bot.lavalink = lavalink.Client(self.bot.user.id)
            for node in self.config["nodes"]:
                self.bot.lavalink.add_node(
                    host=node["host"],
                    port=node["port"],
                    password=node["auth"],
                    region="us",
                    name=node["name"],
                    ssl=node.get("secure", False),
                )
            self.bot.lavalink.add_event_hooks(self)
            logger.success("Lavalink client initialized")

    @lavalink.listener(lavalink.events.TrackStartEvent)
    async def on_track_start(self, event):
        track = event.track
        tts_cnt = event.player.fetch('tts_count') or 0
        if tts_cnt > 0:
            return
        logger.music(f"Now playing: {track.title}", author=track.author, duration=format_time(track.duration))

    @lavalink.listener(lavalink.events.TrackEndEvent)
    async def on_track_end(self, event):
        if getattr(event.track, 'extra', {}).get('tts'):
            player = event.player
            cnt = player.fetch('tts_count') or 0
            player.store('tts_count', max(0, cnt - 1))

    @lavalink.listener(lavalink.events.QueueEndEvent)
    async def on_queue_end(self, event):
        logger.info("Queue ended.")

    @lavalink.listener(lavalink.events.TrackStuckEvent)
    async def on_track_stuck(self, event):
        logger.warning(f"Track stuck: {event.track.title} — auto-skipping")
        await asyncio.sleep(AUTO_SKIP_DELAY)
        player = event.player
        if player.queue:
            await player.skip()
        else:
            await player.stop()

    @lavalink.listener(lavalink.events.TrackExceptionEvent)
    async def on_track_exception(self, event):
        logger.warning(f"Track error: {event.track.title} — auto-skipping")
        await asyncio.sleep(AUTO_SKIP_DELAY)
        player = event.player
        if player.queue:
            await player.skip()
        else:
            await player.stop()

    def _detect_prefix_source(self, query):
        for prefix, source_key in SOURCE_PREFIXES.items():
            if query.lower().startswith(prefix):
                return source_key, query[len(prefix):]
        return None, query

    def _detect_url_source(self, query):
        for pattern, name in URL_PATTERNS:
            if pattern.match(query):
                return name
        return None

    async def _multi_source_search(self, player, query):
        for attempt in range(1, MAX_SEARCH_RETRIES + 1):
            tasks = [player.node.get_tracks(f"{src}:{query}") for src in MULTI_SEARCH_SOURCES]
            try:
                all_results = await asyncio.gather(*tasks, return_exceptions=True)
            except Exception:
                if attempt < MAX_SEARCH_RETRIES:
                    await asyncio.sleep(0.5 * attempt)
                continue

            best_result = None
            best_score = 0
            best_source = None

            for idx, result_data in enumerate(all_results):
                if isinstance(result_data, Exception):
                    continue
                if not result_data or result_data.load_type == LoadType.EMPTY or not result_data.tracks:
                    continue
                track = result_data.tracks[0]
                src_key = MULTI_SEARCH_SOURCES[idx]
                score = _score_result(query, track, src_key)
                if score > best_score:
                    best_score = score
                    best_result = result_data
                    best_source = SOURCE_NAMES.get(src_key, src_key)

            if best_result:
                return best_result, best_source

            if attempt < MAX_SEARCH_RETRIES:
                await asyncio.sleep(0.5 * attempt)

        return None, None

    async def _ensure_connection(self, guild, voice_channel):
        if guild.voice_client:
            try:
                if not guild.voice_client.channel:
                    await guild.voice_client.disconnect(force=True)
                    await voice_channel.connect(cls=LavalinkVoiceClient)
            except Exception:
                try:
                    await guild.voice_client.disconnect(force=True)
                except Exception:
                    pass
                await voice_channel.connect(cls=LavalinkVoiceClient)
        else:
            await voice_channel.connect(cls=LavalinkVoiceClient)

    async def play(self, guild_id, voice_channel_id, query, auto_play=True, response_msg=None, ctx=None):
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return logger.error("Guild not found.")

        voice_channel = guild.get_channel(voice_channel_id)
        if not voice_channel:
            return logger.error("Voice channel not found.")

        player = self.bot.lavalink.player_manager.create(guild_id)
        await self._ensure_connection(guild, voice_channel)

        is_url = bool(url_rx.match(query))
        url_source = self._detect_url_source(query) if is_url else None
        prefix_source, stripped_query = self._detect_prefix_source(query)

        if is_url:
            final_query = query
            source_label = url_source or "URL"
        elif prefix_source:
            final_query = f"{prefix_source}:{stripped_query}"
            source_label = SOURCE_NAMES.get(prefix_source, prefix_source)
        else:
            results, source_label = await self._multi_source_search(player, query)
            if results and results.tracks:
                return await self._handle_results(player, results, query, source_label or "Best Match", auto_play, response_msg, ctx=ctx)
            if response_msg:
                await response_msg.edit(content=None, embed=discord.Embed(description="❌ No results found.", color=0xF04747))
            return logger.error("No results found.")

        results = await player.node.get_tracks(final_query)
        if not results or results.load_type == LoadType.EMPTY or not results.tracks:
            if response_msg:
                await response_msg.edit(content=None, embed=discord.Embed(description="❌ No results found.", color=0xF04747))
            return logger.error("No results found.")

        await self._handle_results(player, results, query, source_label, auto_play, response_msg, ctx=ctx)

    @staticmethod
    def _track_thumbnail(track) -> str | None:
        artwork = getattr(track, 'artwork_url', None)
        if artwork:
            return artwork
        ident = getattr(track, 'identifier', None)
        if ident and len(ident) == 11:
            return f"https://img.youtube.com/vi/{ident}/maxresdefault.jpg"
        return None

    @staticmethod
    def _fmt_duration(ms: int) -> str:
        s = int(ms / 1000)
        m, s = divmod(s, 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

    @staticmethod
    def _mini_bar(pos: int, dur: int, size: int = 14) -> str:
        filled = int((pos / dur) * size) if dur > 0 else 0
        return "▰" * filled + "▱" * (size - filled)

    async def _handle_results(self, player, results, query, source_label, auto_play=True, response_msg=None, ctx=None):
        GREEN  = 0x1DB954
        BLUE   = 0x5865F2
        ORANGE = 0xFA8C1C

        requester = ctx.author if ctx else None
        footer_icon = str(requester.display_avatar.url) if requester else discord.Embed.Empty
        footer_text = f"Requested by {requester.display_name}" if requester else "CYBORG Music"

        if results.load_type == LoadType.PLAYLIST:
            tracks = results.tracks
            for track in tracks:
                player.add(track=track)
            total_ms = sum(t.duration for t in tracks)
            logger.success(f"Added playlist: {results.playlist_info.name} ({len(tracks)} tracks)")
            if response_msg:
                thumb = self._track_thumbnail(tracks[0]) if tracks else None
                embed = discord.Embed(color=ORANGE)
                embed.set_author(name="📋  Playlist Added to Queue")
                embed.title = results.playlist_info.name[:256]
                embed.description = (
                    f"**{len(tracks)} tracks** added\n"
                    f"⏱ Total duration: `{self._fmt_duration(total_ms)}`\n"
                    f"{_source_badge(source_label)}"
                )
                if thumb:
                    embed.set_thumbnail(url=thumb)
                embed.set_footer(text=footer_text, icon_url=footer_icon)
                await response_msg.edit(content=None, embed=embed)
        else:
            track = results.tracks[0]
            player.add(track=track)
            dur_str  = self._fmt_duration(track.duration)
            queue_pos = len(player.queue)
            thumb    = self._track_thumbnail(track)
            uri      = getattr(track, 'uri', None)
            title    = track.title[:256]
            title_md = f"[{title}]({uri})" if uri else title
            logger.success(f"Queued: {track.title}")

            if player.is_playing:
                # ── Added to Queue ────────────────────────────────
                embed = discord.Embed(color=BLUE)
                embed.set_author(name=f"✅  Added to Queue  ·  #{queue_pos}")
                embed.title = title
                if uri:
                    embed.url = uri
                embed.description = (
                    f"by **{track.author}**\n"
                    f"⏱ `{dur_str}`  ·  {_source_badge(source_label)}"
                )
                if thumb:
                    embed.set_thumbnail(url=thumb)
                embed.set_footer(text=footer_text, icon_url=footer_icon)
            else:
                # ── Now Playing ───────────────────────────────────
                BAR = 14
                bar_chars = ["▬"] * BAR
                bar_chars[0] = "🔘"
                bar = "".join(bar_chars)

                embed = discord.Embed(color=GREEN)
                embed.set_author(name="▶  Now Playing")
                embed.title = title
                if uri:
                    embed.url = uri
                embed.description = (
                    f"by **{track.author}**\n\n"
                    f"`00:00` {bar} `{dur_str}`\n\n"
                    f"{_source_badge(source_label)}"
                )
                if thumb:
                    embed.set_thumbnail(url=thumb)
                embed.set_footer(text=footer_text, icon_url=footer_icon)

            if response_msg:
                await response_msg.edit(content=None, embed=embed)

        if auto_play and not player.is_playing:
            await player.play()

    async def start_playback(self, guild_id):
        player = self.bot.lavalink.player_manager.get(guild_id)
        if player and not player.is_playing and player.queue:
            await player.play()

    async def skip(self, guild_id):
        player = self.bot.lavalink.player_manager.get(guild_id)
        if not player or not player.is_playing:
            return logger.error("Nothing is playing.")
        title = player.current.title if player.current else "current track"
        if not player.queue:
            await player.stop()
        else:
            await player.skip()
        logger.success(f"Skipped: {title}")

    async def stop(self, guild_id):
        player = self.bot.lavalink.player_manager.get(guild_id)
        if not player:
            return
        guild = self.bot.get_guild(guild_id)
        player.queue.clear()
        await player.stop()
        if guild and guild.voice_client:
            await guild.voice_client.disconnect(force=True)
        self.active_filter.pop(guild_id, None)

    async def disconnect(self, guild_id):
        guild = self.bot.get_guild(guild_id)
        if not guild or not guild.voice_client:
            return
        player = self.bot.lavalink.player_manager.get(guild_id)
        if player:
            player.queue.clear()
            await player.stop()
        await guild.voice_client.disconnect(force=True)
        self.active_filter.pop(guild_id, None)

    async def set_volume(self, guild_id, volume):
        player = self.bot.lavalink.player_manager.get(guild_id)
        if not player:
            return
        await player.set_volume(volume)

    async def apply_filter_cmd(self, guild_id, filter_name):
        player = self.bot.lavalink.player_manager.get(guild_id)
        if not player:
            return logger.error("Nothing is playing.")
        if filter_name == "clear":
            await clear_filters(player)
            self.active_filter.pop(guild_id, None)
        else:
            applied = await apply_filter(player, filter_name)
            if applied:
                self.active_filter[guild_id] = filter_name

    async def seek(self, guild_id, position_seconds):
        player = self.bot.lavalink.player_manager.get(guild_id)
        if not player or not player.current:
            return
        await player.seek(position_seconds * 1000)

    async def pause(self, guild_id):
        player = self.bot.lavalink.player_manager.get(guild_id)
        if not player:
            return
        await player.set_pause(not player.paused)

    async def shuffle(self, guild_id):
        player = self.bot.lavalink.player_manager.get(guild_id)
        if not player or len(player.queue) <= 1:
            return
        random.shuffle(player.queue)

    async def loop(self, guild_id, mode):
        player = self.bot.lavalink.player_manager.get(guild_id)
        if not player:
            return
        mode = mode.lower()
        if mode == "track":
            player.set_loop(player.LOOP_SINGLE)
        elif mode == "queue":
            player.set_loop(player.LOOP_QUEUE)
        elif mode in ("off", "disable", "none"):
            player.set_loop(player.LOOP_NONE)

    async def clear_queue(self, guild_id):
        player = self.bot.lavalink.player_manager.get(guild_id)
        if player:
            player.queue.clear()

    async def replay(self, guild_id):
        player = self.bot.lavalink.player_manager.get(guild_id)
        if not player or not player.current:
            return
        player.queue.insert(0, player.current)
