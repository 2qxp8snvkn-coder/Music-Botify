import re
import random
import asyncio
import aiohttp
import lavalink
from lavalink.server import LoadType
from colorama import Fore, Style
from pystyle import Colors, Colorate
from core.voice import LavalinkVoiceClient
from core.filters import apply_filter, clear_filters, list_filters
from core.logger import logger
from core.utils import format_time, create_progress_bar, PRIMARY, SECONDARY
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
    def __init__(self, client, config):
        self.client = client
        self.config = config
        self.active_filter = {}
        self.context_guild = None
        self.context_voice = None
        self._http_session = None
        self.tts = TTSManager(self)

    async def _get_session(self):
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession()
        return self._http_session

    async def close(self):
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
            self._http_session = None

    def setup_lavalink(self):
        self.client.lavalink = lavalink.Client(self.client.user.id)
        for node in self.config["nodes"]:
            self.client.lavalink.add_node(
                host=node["host"],
                port=node["port"],
                password=node["auth"],
                region="us",
                name=node["name"],
            )
        self.client.lavalink.add_event_hooks(self)

    @lavalink.listener(lavalink.events.TrackStartEvent)
    async def on_track_start(self, event):
        player = event.player
        tts_cnt = player.fetch('tts_count') or 0
        if tts_cnt > 0:
            return
        track = event.track
        logger.music(f"Now playing: {track.title}", author=track.author, duration=format_time(track.duration))

    @lavalink.listener(lavalink.events.TrackEndEvent)
    async def on_track_end(self, event):
        if getattr(event.track, 'extra', {}).get('tts'):
            player = event.player
            cnt = player.fetch('tts_count') or 0
            player.store('tts_count', max(0, cnt - 1))

    @lavalink.listener(lavalink.events.QueueEndEvent)
    async def on_queue_end(self, event):
        logger.info("Queue ended. Staying in voice channel.")

    @lavalink.listener(lavalink.events.TrackStuckEvent)
    async def on_track_stuck(self, event):
        if getattr(event.track, 'extra', {}).get('tts'):
            player = event.player
            cnt = player.fetch('tts_count') or 0
            player.store('tts_count', max(0, cnt - 1))
            if player.queue:
                await player.skip()
            else:
                await player.stop()
            return
        track = event.track
        logger.warning(f"Track stuck: {track.title} — auto-skipping in {AUTO_SKIP_DELAY}s")
        await asyncio.sleep(AUTO_SKIP_DELAY)
        player = event.player
        if player.queue:
            await player.skip()
        else:
            await player.stop()

    @lavalink.listener(lavalink.events.TrackExceptionEvent)
    async def on_track_exception(self, event):
        if getattr(event.track, 'extra', {}).get('tts'):
            player = event.player
            cnt = player.fetch('tts_count') or 0
            player.store('tts_count', max(0, cnt - 1))
            if player.queue:
                await player.skip()
            else:
                await player.stop()
            return
        track = event.track
        logger.warning(f"Track error: {track.title} — auto-skipping in {AUTO_SKIP_DELAY}s")
        await asyncio.sleep(AUTO_SKIP_DELAY)
        player = event.player
        if player.queue:
            await player.skip()
        else:
            await player.stop()

    async def _update_voice_status(self, channel_id, status):
        if not channel_id or not status:
            return
        try:
            session = await self._get_session()
            async with session.put(
                f"https://discord.com/api/v9/channels/{channel_id}/voice-status",
                json={"status": status},
                headers={
                    "Authorization": self.client.http.token,
                    "Content-Type": "application/json",
                },
            ) as resp:
                await resp.read()
        except Exception:
            pass

    def set_context(self, guild_id, voice_id=None):
        self.context_guild = guild_id
        if voice_id:
            self.context_voice = voice_id

    def get_guild_id(self, args, idx=0):
        if len(args) > idx:
            try:
                return int(args[idx])
            except ValueError:
                pass
        return self.context_guild

    def _detect_url_source(self, query):
        for pattern, name in URL_PATTERNS:
            if pattern.match(query):
                return name
        return None

    def _detect_prefix_source(self, query):
        for prefix, source_key in SOURCE_PREFIXES.items():
            if query.lower().startswith(prefix):
                return source_key, query[len(prefix):]
        return None, query

    async def _multi_source_search(self, player, query):
        for attempt in range(1, MAX_SEARCH_RETRIES + 1):
            logger.info(f"Searching across {len(MULTI_SEARCH_SOURCES)} sources (attempt {attempt})...")
            tasks = [player.node.get_tracks(f"{src}:{query}") for src in MULTI_SEARCH_SOURCES]
            try:
                all_results = await asyncio.gather(*tasks, return_exceptions=True)
            except Exception:
                if attempt < MAX_SEARCH_RETRIES:
                    await asyncio.sleep(0.5 * attempt)
                    continue
                return None, None

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
                    logger.warning("Stale voice client detected — reconnecting...")
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

    async def join(self, guild_id, voice_channel_id):
        guild = self.client.get_guild(guild_id)
        if not guild:
            return logger.error("Guild not found.")
        voice_channel = guild.get_channel(voice_channel_id)
        if not voice_channel:
            return logger.error("Voice channel not found.")
        if guild.voice_client:
            return logger.info(f"Already connected to {voice_channel.name}.")
        self.client.lavalink.player_manager.create(guild_id)
        await voice_channel.connect(cls=LavalinkVoiceClient)
        logger.success(f"Joined: {voice_channel.name}")

    async def play(self, guild_id, voice_channel_id, query, auto_play=True):
        guild = self.client.get_guild(guild_id)
        if not guild:
            return logger.error("Guild not found.")

        voice_channel = guild.get_channel(voice_channel_id)
        if not voice_channel:
            return logger.error("Voice channel not found.")

        player = self.client.lavalink.player_manager.create(guild_id)

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
            logger.info(f"Searching {source_label}: {stripped_query}")
        else:
            results, source_label = await self._multi_source_search(player, query)
            if results and results.tracks:
                return await self._handle_results(player, results, query, source_label or "Best Match", auto_play=auto_play)
            return logger.error("No results found across any source.")

        results = await player.node.get_tracks(final_query)
        if not results or results.load_type == LoadType.EMPTY or not results.tracks:
            return logger.error("No results found.")

        await self._handle_results(player, results, query, source_label, auto_play=auto_play)

    async def _handle_results(self, player, results, query, source_label, auto_play=True):
        if results.load_type == LoadType.PLAYLIST:
            tracks = results.tracks
            for track in tracks:
                player.add(track=track)
            logger.success(
                f"Added playlist: {results.playlist_info.name}",
                tracks=len(tracks), source=source_label,
            )
        else:
            track = results.tracks[0]
            duration_sec = track.duration / 1000
          #  if duration_sec < 60:
          #      return logger.warning("Track too short (under 1 minute). Skipped.")
            player.add(track=track)
            queue_pos = len(player.queue)
            logger.success(
                f"Queued: {track.title}",
                author=track.author, duration=format_time(track.duration),
                source=source_label, position=queue_pos,
            )

        if auto_play and not player.is_playing:
            await player.play()

    async def start_playback(self, guild_id):
        player = self.client.lavalink.player_manager.get(guild_id)
        if player and not player.is_playing and player.queue:
            await player.play()

    async def skip(self, guild_id):
        player = self.client.lavalink.player_manager.get(guild_id)
        if not player or not player.is_playing:
            return logger.error("Nothing is playing.")
        title = player.current.title if player.current else "current track"
        if not player.queue:
            await player.stop()
        else:
            await player.skip()
        logger.success(f"Skipped: {title}")

    async def stop(self, guild_id):
        player = self.client.lavalink.player_manager.get(guild_id)
        if not player:
            return logger.error("Nothing is playing.")
        guild = self.client.get_guild(guild_id)
        player.queue.clear()
        await player.stop()
        if guild and guild.voice_client:
            await guild.voice_client.disconnect(force=True)
        self.active_filter.pop(guild_id, None)
        logger.success("Stopped and cleared queue.")

    async def disconnect(self, guild_id):
        guild = self.client.get_guild(guild_id)
        if not guild or not guild.voice_client:
            return logger.error("Not connected to any voice channel.")
        player = self.client.lavalink.player_manager.get(guild_id)
        if player:
            player.queue.clear()
            await player.stop()
        await guild.voice_client.disconnect(force=True)
        self.active_filter.pop(guild_id, None)
        logger.success("Disconnected from voice channel.")

    def now_playing(self, guild_id):
        player = self.client.lavalink.player_manager.get(guild_id)
        if not player or not player.current:
            return logger.error("Nothing is playing.")

        track = player.current
        position = player.position
        duration = track.duration
        bar = create_progress_bar(position, duration)
        time_str = f"{format_time(position)} / {format_time(duration)}" if duration > 0 else format_time(position)
        state = f"{Fore.RED}Paused{Style.RESET_ALL}" if player.paused else f"{Fore.GREEN}Playing{Style.RESET_ALL}"
        current_filter = self.active_filter.get(guild_id, "none")
        vol = player.volume
        loop_mode = "off"
        if player.loop == player.LOOP_SINGLE:
            loop_mode = "track"
        elif player.loop == player.LOOP_QUEUE:
            loop_mode = "queue"

        logger.separator()
        print(f"  {Fore.WHITE}{Style.BRIGHT}{track.title}{Style.RESET_ALL}")
        print(f"  {Fore.LIGHTBLACK_EX}{track.author}{Style.RESET_ALL}")
        print()
        print(f"  {bar}")
        print(f"  {Fore.LIGHTBLACK_EX}{time_str}{Style.RESET_ALL}")
        print()
        print(
            f"  {Fore.LIGHTBLACK_EX}State:{Style.RESET_ALL} {state}    "
            f"{Fore.LIGHTBLACK_EX}Vol:{Style.RESET_ALL} {Fore.CYAN}{vol}{Style.RESET_ALL}    "
            f"{Fore.LIGHTBLACK_EX}Loop:{Style.RESET_ALL} {Fore.CYAN}{loop_mode}{Style.RESET_ALL}    "
            f"{Fore.LIGHTBLACK_EX}Filter:{Style.RESET_ALL} {Fore.MAGENTA}{current_filter}{Style.RESET_ALL}"
        )
        logger.separator()

    def get_queue(self, guild_id):
        player = self.client.lavalink.player_manager.get(guild_id)
        if not player:
            return logger.error("Nothing is playing.")

        logger.separator()
        print(Colorate.Horizontal(PRIMARY, "  QUEUE".center(40)))
        logger.separator()

        if player.current:
            print(f"  {Fore.GREEN}Now:{Style.RESET_ALL} {Style.BRIGHT}{player.current.title}{Style.RESET_ALL} {Fore.LIGHTBLACK_EX}[{format_time(player.current.duration)}]{Style.RESET_ALL}")
            print()

        if player.queue:
            for i, track in enumerate(player.queue[:20]):
                num = f"{Fore.CYAN}{i + 1:>3}.{Style.RESET_ALL}"
                dur = f"{Fore.LIGHTBLACK_EX}[{format_time(track.duration)}]{Style.RESET_ALL}"
                print(f"  {num} {track.title} {dur}")
            if len(player.queue) > 20:
                print(f"  {Fore.LIGHTBLACK_EX}... and {len(player.queue) - 20} more{Style.RESET_ALL}")
        else:
            print(f"  {Fore.LIGHTBLACK_EX}Queue is empty.{Style.RESET_ALL}")

        print(f"\n  {Fore.LIGHTBLACK_EX}Total: {len(player.queue)} track(s){Style.RESET_ALL}")
        logger.separator()

    async def set_volume(self, guild_id, volume=None):
        player = self.client.lavalink.player_manager.get(guild_id)
        if not player:
            return logger.error("Nothing is playing.")
        if volume is None:
            return logger.info(f"Current volume: {player.volume}")
        if volume < 1 or volume > 1000:
            return logger.error("Volume must be between 1 and 1000.")
        await player.set_volume(volume)
        bar_len = min(volume // 20, 50)
        bar = f"{Fore.GREEN}{'|' * bar_len}{Fore.LIGHTBLACK_EX}{'|' * (50 - bar_len)}{Style.RESET_ALL}"
        print(f"  {bar} {Fore.CYAN}{volume}{Style.RESET_ALL}")

    async def apply_filter_cmd(self, guild_id, filter_name):
        player = self.client.lavalink.player_manager.get(guild_id)
        if not player:
            return logger.error("Nothing is playing.")
        if filter_name == "clear":
            await clear_filters(player)
            self.active_filter.pop(guild_id, None)
            return logger.success("Cleared all filters.")
        applied = await apply_filter(player, filter_name)
        if applied:
            self.active_filter[guild_id] = filter_name
            logger.success(f"Applied filter: {filter_name}")
        else:
            logger.error(f"Unknown filter. Available: {', '.join(list_filters())}")

    async def seek(self, guild_id, position_seconds):
        player = self.client.lavalink.player_manager.get(guild_id)
        if not player or not player.current:
            return logger.error("Nothing is playing.")
        await player.seek(position_seconds * 1000)
        logger.success(f"Seeked to {format_time(position_seconds * 1000)}")

    async def pause(self, guild_id):
        player = self.client.lavalink.player_manager.get(guild_id)
        if not player:
            return logger.error("Nothing is playing.")
        await player.set_pause(not player.paused)
        state = "Paused" if player.paused else "Resumed"
        logger.info(f"Playback: {state}")

    async def shuffle(self, guild_id):
        player = self.client.lavalink.player_manager.get(guild_id)
        if not player:
            return logger.error("Nothing is playing.")
        if len(player.queue) <= 1:
            return logger.warning("Need more than 1 track in queue to shuffle.")
        random.shuffle(player.queue)
        logger.success(f"Shuffled {len(player.queue)} tracks.")

    async def loop(self, guild_id, mode):
        player = self.client.lavalink.player_manager.get(guild_id)
        if not player:
            return logger.error("Nothing is playing.")
        mode = mode.lower()
        if mode == "track":
            player.set_loop(player.LOOP_SINGLE)
            logger.success("Looping: current track")
        elif mode == "queue":
            player.set_loop(player.LOOP_QUEUE)
            logger.success("Looping: entire queue")
        elif mode in ("off", "disable", "none"):
            player.set_loop(player.LOOP_NONE)
            logger.success("Looping disabled.")
        else:
            logger.error("Invalid mode. Use: track, queue, or off")

    async def clear_queue(self, guild_id):
        player = self.client.lavalink.player_manager.get(guild_id)
        if not player:
            return logger.error("Nothing is playing.")
        count = len(player.queue)
        player.queue.clear()
        logger.success(f"Cleared {count} track(s) from queue.")

    async def replay(self, guild_id):
        player = self.client.lavalink.player_manager.get(guild_id)
        if not player or not player.current:
            return logger.error("Nothing is playing.")
        track = player.current
        player.queue.insert(0, track)
        logger.success(f"Pushed {track.title} to queue #1.")

    def list_guilds(self):
        guilds = self.client.guilds
        if not guilds:
            return logger.error("Not in any guilds.")
        logger.separator()
        print(Colorate.Horizontal(PRIMARY, "  GUILDS".center(40)))
        logger.separator()
        for g in guilds:
            print(f"  {Fore.CYAN}*{Style.RESET_ALL} {g.name} {Fore.LIGHTBLACK_EX}({g.id}) - {g.member_count} members{Style.RESET_ALL}")
        print(f"\n  {Fore.LIGHTBLACK_EX}Total: {len(guilds)} guild(s){Style.RESET_ALL}")
        logger.separator()

    def show_nodes(self):
        logger.separator()
        print(Colorate.Horizontal(PRIMARY, "  LAVALINK NODES".center(40)))
        logger.separator()
        for node in self.client.lavalink.node_manager.nodes:
            status = f"{Fore.GREEN}Connected{Style.RESET_ALL}" if node.available else f"{Fore.RED}Offline{Style.RESET_ALL}"
            host = getattr(node._transport, '_host', '?')
            port = getattr(node._transport, '_port', '?')
            print(f"  {Fore.CYAN}*{Style.RESET_ALL} {node.name} {Fore.LIGHTBLACK_EX}({host}:{port}){Style.RESET_ALL} [{status}]")
        logger.separator()
