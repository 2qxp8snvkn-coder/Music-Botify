import asyncio
import sys
import discord
from colorama import Fore, Style
from pystyle import Colors, Colorate
from core.filters import list_filters
from core.logger import logger
from core.utils import banner, menu_box, PRIMARY, SECONDARY


HELP_SECTIONS = [
    ("MUSIC", [
        "[join]                         - Join voice channel",
        "[play]    <query/url>          - Play a song or playlist",
        "[skip]                         - Skip current track",
        "[stop]                         - Stop and clear queue",
        "[dc]                           - Disconnect from voice",
        "[np]                           - Now playing details",
        "[queue]                        - Show current queue",
        "[volume]  [1-1000]             - Get/set volume",
        "[seek]    <seconds>            - Seek to position",
        "[pause]                        - Toggle pause/resume",
    ]),
    ("QUEUE", [
        "[shuffle]                      - Shuffle the queue",
        "[loop]    <track/queue/off>    - Set loop mode",
        "[clear]                        - Clear the queue",
        "[replay]                       - Push current to #1",
    ]),
    ("FILTERS", [
        "[filter]  <name>               - Apply an audio filter",
        "[filter]  clear                - Remove all filters",
        "[filters]                      - List available filters",
    ]),
    ("SOURCES (use with play)", [
        "[yt] YouTube   [sp] Spotify   [sc] SoundCloud",
        "[js] JioSaavn  [am] Apple     [dz] Deezer",
        "Example: play sp shape of you",
        "No prefix = auto-searches all sources",
    ]),
    ("HINDI TTS", [
        "[tts]      <text>              - Speak Hindi text in voice",
        "[ttsvoice] <swara/madhur>      - Switch Hindi TTS voice",
        "[ttsstop]                      - Stop TTS and disconnect",
        "[ttsvoices]                    - List available Hindi voices",
    ]),
    ("SESSION", [
        "[use]     <guild_id> [vc_id]   - Set active guild (all bots)",
        "[bots]                         - List all bots",
        "[guilds]                       - List all guilds",
        "[nodes]                        - Lavalink node status",
        "[status]  <online/idle/dnd>    - Change presence",
    ]),
    ("SYSTEM", [
        "[help]                         - Show this help",
        "[exit]                         - Shut down",
    ]),
]


def show_help():
    for title, items in HELP_SECTIONS:
        menu_box(title, items)


class CLI:
    def __init__(self, player_managers, clients):
        self.all_pm = player_managers
        self.all_clients = clients
        self.running = True

    async def _run_all(self, coro_fn):
        tasks = []
        for i, pm in enumerate(self.all_pm):
            tasks.append(coro_fn(pm, self.all_clients[i]))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.error(f"Bot #{i+1}: {r}")

    async def start(self):
        banner()
        show_help()
        count = len(self.all_clients)
        if count > 1:
            logger.info(f"{count} bots loaded. All commands apply to all bots simultaneously.")
        logger.info("Type 'help' for commands or 'use <guild_id> <voice_id>' to set context.")
        loop = asyncio.get_event_loop()

        while self.running:
            try:
                sys.stdout.write(Colorate.Horizontal(PRIMARY, " cyborg > "))
                sys.stdout.flush()
                line = await loop.run_in_executor(None, sys.stdin.readline)
                line = line.strip()
                if not line:
                    continue
                await self.handle(line)
            except (EOFError, KeyboardInterrupt):
                self.running = False
                break
            except Exception as e:
                logger.error(str(e))

    async def handle(self, line):
        parts = line.split()
        cmd = parts[0].lower()
        args = parts[1:]

        try:
            if cmd == "help":
                show_help()

            elif cmd == "exit":
                logger.warning("Shutting down...")
                self.running = False
                for pm in self.all_pm:
                    try:
                        await pm.close()
                    except Exception:
                        pass
                for c in self.all_clients:
                    if hasattr(c, "lavalink"):
                        try:
                            await c.lavalink.close()
                        except Exception:
                            pass
                    await c.close()

            elif cmd == "bots":
                logger.separator()
                print(Colorate.Horizontal(PRIMARY, "  BOTS".center(40)))
                logger.separator()
                for i, c in enumerate(self.all_clients):
                    ctx = self.all_pm[i]
                    guild_name = ""
                    if ctx.context_guild:
                        g = c.get_guild(ctx.context_guild)
                        guild_name = f" -> {g.name}" if g else ""
                    node_name = getattr(self.all_pm[i], 'node_name', '?')
                    print(f"  {Fore.CYAN}#{i+1}{Style.RESET_ALL} {c.user} {Fore.LIGHTBLACK_EX}({len(c.guilds)} guilds) [{node_name}]{guild_name}{Style.RESET_ALL}")
                logger.separator()

            elif cmd == "use":
                if len(args) < 1:
                    return logger.error("Usage: use <guild_id> [voice_channel_id]")
                guild_id = int(args[0])
                voice_id = int(args[1]) if len(args) > 1 else None
                for i, pm in enumerate(self.all_pm):
                    guild = self.all_clients[i].get_guild(guild_id)
                    if guild:
                        pm.set_context(guild_id, voice_id)
                        ch_name = ""
                        if voice_id:
                            ch = guild.get_channel(voice_id)
                            ch_name = ch.name if ch else str(voice_id)
                        logger.success(f"Bot #{i+1} context set: {guild.name}", channel=ch_name or "none")
                    else:
                        logger.warning(f"Bot #{i+1}: Guild {guild_id} not found, skipped.")

            elif cmd == "join":
                async def _join(pm, client):
                    if not pm.context_guild or not pm.context_voice:
                        return
                    await pm.join(pm.context_guild, pm.context_voice)
                await self._run_all(_join)

            elif cmd == "play":
                if not args:
                    return logger.error("Usage: play <query/url>")
                query = " ".join(args)
                # Phase 1: All bots search and queue the track (don't start playing yet)
                async def _play_queue(pm, client):
                    if not pm.context_guild or not pm.context_voice:
                        return
                    await pm.play(pm.context_guild, pm.context_voice, query, auto_play=False)
                await self._run_all(_play_queue)
                # Phase 2: Start playback on ALL bots simultaneously for voice sync
                async def _play_start(pm, client):
                    if not pm.context_guild:
                        return
                    await pm.start_playback(pm.context_guild)
                await self._run_all(_play_start)

            elif cmd == "skip":
                async def _skip(pm, client):
                    gid = pm.context_guild
                    if gid:
                        await pm.skip(gid)
                await self._run_all(_skip)

            elif cmd == "stop":
                async def _stop(pm, client):
                    gid = pm.context_guild
                    if gid:
                        await pm.stop(gid)
                await self._run_all(_stop)

            elif cmd in ("dc", "disconnect", "leave"):
                async def _dc(pm, client):
                    gid = pm.context_guild
                    if gid:
                        await pm.disconnect(gid)
                await self._run_all(_dc)

            elif cmd in ("np", "nowplaying", "now"):
                for i, pm in enumerate(self.all_pm):
                    gid = pm.context_guild
                    if gid:
                        if len(self.all_pm) > 1:
                            logger.info(f"Bot #{i+1}:")
                        pm.now_playing(gid)

            elif cmd in ("queue", "q"):
                for i, pm in enumerate(self.all_pm):
                    gid = pm.context_guild
                    if gid:
                        if len(self.all_pm) > 1:
                            logger.info(f"Bot #{i+1}:")
                        pm.get_queue(gid)

            elif cmd in ("volume", "vol"):
                vol = int(args[0]) if args else None
                async def _vol(pm, client):
                    gid = pm.context_guild
                    if gid:
                        await pm.set_volume(gid, vol)
                await self._run_all(_vol)

            elif cmd == "seek":
                if not args:
                    return logger.error("Usage: seek <seconds>")
                secs = int(args[0])
                async def _seek(pm, client):
                    gid = pm.context_guild
                    if gid:
                        await pm.seek(gid, secs)
                await self._run_all(_seek)

            elif cmd == "pause":
                async def _pause(pm, client):
                    gid = pm.context_guild
                    if gid:
                        await pm.pause(gid)
                await self._run_all(_pause)

            elif cmd == "shuffle":
                async def _shuffle(pm, client):
                    gid = pm.context_guild
                    if gid:
                        await pm.shuffle(gid)
                await self._run_all(_shuffle)

            elif cmd == "loop":
                if not args:
                    return logger.error("Usage: loop <track/queue/off>")
                mode = args[0]
                async def _loop(pm, client):
                    gid = pm.context_guild
                    if gid:
                        await pm.loop(gid, mode)
                await self._run_all(_loop)

            elif cmd in ("clear", "clearqueue"):
                async def _clear(pm, client):
                    gid = pm.context_guild
                    if gid:
                        await pm.clear_queue(gid)
                await self._run_all(_clear)

            elif cmd in ("replay", "pushfirst"):
                async def _replay(pm, client):
                    gid = pm.context_guild
                    if gid:
                        await pm.replay(gid)
                await self._run_all(_replay)

            elif cmd == "filter":
                if not args:
                    return logger.error("Usage: filter <name> or filter clear")
                fname = args[0].lower()
                async def _filter(pm, client):
                    gid = pm.context_guild
                    if gid:
                        await pm.apply_filter_cmd(gid, fname)
                await self._run_all(_filter)

            elif cmd == "filters":
                available = list_filters()
                logger.separator()
                print(Colorate.Horizontal(PRIMARY, "  AVAILABLE FILTERS".center(40)))
                logger.separator()
                for f in available:
                    print(f"  {Fore.MAGENTA}*{Style.RESET_ALL} {f}")
                logger.separator()

            elif cmd == "guilds":
                for i, pm in enumerate(self.all_pm):
                    if len(self.all_pm) > 1:
                        logger.info(f"Bot #{i+1}:")
                    pm.list_guilds()

            elif cmd == "nodes":
                self.all_pm[0].show_nodes()

            elif cmd == "tts":
                if not args:
                    return logger.error("Usage: tts <text>")
                text = " ".join(args)
                first_pm = None
                for pm in self.all_pm:
                    if pm.context_guild:
                        first_pm = pm
                        break
                if not first_pm:
                    return logger.error("No context set. Use 'use <guild_id> <vc_id>' first.")
                url = await first_pm.tts.resolve_url(first_pm.context_guild, text)
                if not url:
                    return
                async def _tts(pm, client):
                    if not pm.context_guild or not pm.context_voice:
                        return
                    await pm.tts.play_url(pm.context_guild, pm.context_voice, url)
                await self._run_all(_tts)

            elif cmd == "ttsvoice":
                if not args:
                    for pm in self.all_pm:
                        if pm.context_guild:
                            pm.tts.show_voice(pm.context_guild)
                            break
                    return
                key = args[0].lower()
                for i, pm in enumerate(self.all_pm):
                    gid = pm.context_guild
                    if gid:
                        if pm.tts.set_voice(gid, key):
                            logger.success(f"Bot #{i+1} TTS voice set to: {key.capitalize()}")
                        else:
                            logger.error(f"Unknown voice '{key}'. Available: swara, madhur")

            elif cmd == "ttsstop":
                async def _ttsstop(pm, client):
                    gid = pm.context_guild
                    if gid:
                        await pm.tts.stop(gid)
                await self._run_all(_ttsstop)

            elif cmd == "ttsvoices":
                self.all_pm[0].tts.list_voices()

            elif cmd == "status":
                if not args:
                    return logger.error("Usage: status <online|idle|dnd|invisible>")
                status_type = args[0].lower()
                valid = ("online", "idle", "dnd", "invisible")
                if status_type not in valid:
                    return logger.error(f"Invalid. Choose: {', '.join(valid)}")
                status_map = {
                    "online": discord.Status.online,
                    "idle": discord.Status.idle,
                    "dnd": discord.Status.dnd,
                    "invisible": discord.Status.invisible,
                }
                for c in self.all_clients:
                    await c.change_presence(status=status_map[status_type])
                logger.success(f"Status: {status_type} (all bots)")

            else:
                logger.error(f"Unknown command: {cmd}. Type 'help' for commands.")

        except Exception as e:
            logger.error(str(e))
