import os
import sys
import json
import asyncio
import ctypes
import discord
from core.player import PlayerManager
from core.cli import CLI
from core.logger import logger
from core.utils import truncate_token
from core.filters import list_filters


def load_config():
    base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    config_path = os.path.join(base_dir, "config.json")
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
    except FileNotFoundError:
        logger.error(f"config.json not found at {config_path}.")
        sys.exit(1)
    except json.JSONDecodeError as e:
        logger.error(f"config.json is invalid JSON: {e}")
        sys.exit(1)
    if "nodes" not in config or not config["nodes"]:
        logger.error("config.json must contain a non-empty 'nodes' list.")
        sys.exit(1)
    return config


CONFIG = load_config()
PREFIX = "!"


def set_title(text):
    if os.name == "nt":
        ctypes.windll.kernel32.SetConsoleTitleW(text)
    else:
        print(f"\033]0;{text}\007", end="", flush=True)


def load_tokens():
    try:
        with open("tokens.txt", "r") as f:
            tokens = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        logger.error("tokens.txt not found.")
        sys.exit(1)
    if not tokens:
        logger.error("No tokens found in tokens.txt.")
        sys.exit(1)
    return tokens


def remove_invalid_token(token):
    try:
        with open("tokens.txt", "r") as f:
            lines = f.readlines()
        with open("tokens.txt", "w") as f:
            for line in lines:
                if line.strip() != token:
                    f.write(line)
        logger.warning(f"Removed invalid token ...{truncate_token(token)} from tokens.txt")
    except Exception as e:
        logger.error(f"Failed to remove token: {e}")


clients = []
player_managers = []
ready_count = 0
total_clients = 0
cli_started = False
cli_task = None

HELP_MSG = """**🎵 CYBORG Music Commands**

**Music**
`!use <guild_id> <vc_id>` — set server & voice channel
`!join` — join voice channel
`!play <song/url>` — play music
`!skip` — skip track
`!stop` — stop & clear queue
`!dc` — disconnect
`!np` — now playing
`!queue` — show queue
`!volume [1-1000]` — get/set volume
`!pause` — pause/resume

**Queue**
`!shuffle` — shuffle queue
`!loop <track/queue/off>` — loop mode
`!clear` — clear queue
`!replay` — replay current track

**Filters**
`!filter <name>` — apply filter
`!filter clear` — remove filters
`!filters` — list all filters

**Sources** (use with !play)
`sp ` Spotify · `yt ` YouTube · `sc ` SoundCloud
`js ` JioSaavn · `am ` Apple Music · `dz ` Deezer
Example: `!play sp shape of you`

`!help` — show this message"""


async def handle_message_command(message, pm):
    content = message.content.strip()
    if not content.startswith(PREFIX):
        return
    parts = content[len(PREFIX):].split()
    if not parts:
        return
    cmd = parts[0].lower()
    args = parts[1:]

    async def reply(text):
        try:
            await message.channel.send(text)
        except Exception:
            pass

    gid = pm.context_guild
    vid = pm.context_voice

    if cmd == "help":
        await reply(HELP_MSG)

    elif cmd == "use":
        if len(args) < 1:
            return await reply("❌ Usage: `!use <guild_id> <voice_channel_id>`")
        try:
            guild_id = int(args[0])
            voice_id = int(args[1]) if len(args) > 1 else None
            pm.set_context(guild_id, voice_id)
            guild = pm.client.get_guild(guild_id)
            g_name = guild.name if guild else str(guild_id)
            ch_name = ""
            if voice_id and guild:
                ch = guild.get_channel(voice_id)
                ch_name = f" → #{ch.name}" if ch else f" → {voice_id}"
            await reply(f"✅ Context set: **{g_name}**{ch_name}")
        except ValueError:
            await reply("❌ IDs must be numbers.")

    elif cmd == "join":
        if not gid or not vid:
            return await reply("❌ Set context first: `!use <guild_id> <vc_id>`")
        await pm.join(gid, vid)
        await reply("✅ Joined voice channel!")

    elif cmd == "play":
        if not args:
            return await reply("❌ Usage: `!play <song or url>`")
        if not gid or not vid:
            return await reply("❌ Set context first: `!use <guild_id> <vc_id>`")
        query = " ".join(args)
        await reply(f"🔍 Searching: **{query}**...")
        await pm.play(gid, vid, query, auto_play=True)

    elif cmd == "skip":
        if not gid:
            return await reply("❌ No context set.")
        await pm.skip(gid)
        await reply("⏭ Skipped!")

    elif cmd == "stop":
        if not gid:
            return await reply("❌ No context set.")
        await pm.stop(gid)
        await reply("⏹ Stopped and cleared queue.")

    elif cmd in ("dc", "disconnect", "leave"):
        if not gid:
            return await reply("❌ No context set.")
        await pm.disconnect(gid)
        await reply("👋 Disconnected.")

    elif cmd in ("np", "nowplaying"):
        if not gid:
            return await reply("❌ No context set.")
        player = pm.client.lavalink.player_manager.get(gid)
        if not player or not player.current:
            return await reply("❌ Nothing is playing.")
        from core.utils import format_time, create_progress_bar
        t = player.current
        pos = player.position
        dur = t.duration
        mins_pos, secs_pos = divmod(int(pos / 1000), 60)
        mins_dur, secs_dur = divmod(int(dur / 1000), 60)
        bar_fill = int((pos / dur) * 20) if dur > 0 else 0
        bar = "▓" * bar_fill + "░" * (20 - bar_fill)
        state = "⏸ Paused" if player.paused else "▶ Playing"
        filt = pm.active_filter.get(gid, "none")
        await reply(
            f"**{t.title}**\n{t.author}\n"
            f"`{bar}` {mins_pos:02d}:{secs_pos:02d}/{mins_dur:02d}:{secs_dur:02d}\n"
            f"{state} · Vol: {player.volume} · Filter: {filt}"
        )

    elif cmd in ("queue", "q"):
        if not gid:
            return await reply("❌ No context set.")
        player = pm.client.lavalink.player_manager.get(gid)
        if not player:
            return await reply("❌ Nothing is playing.")
        from core.utils import format_time
        lines = []
        if player.current:
            lines.append(f"▶ **{player.current.title}**")
        if player.queue:
            for i, t in enumerate(player.queue[:10]):
                mins, secs = divmod(int(t.duration / 1000), 60)
                lines.append(f"`{i+1}.` {t.title} [{mins:02d}:{secs:02d}]")
            if len(player.queue) > 10:
                lines.append(f"_...and {len(player.queue)-10} more_")
        else:
            lines.append("Queue is empty.")
        await reply("\n".join(lines))

    elif cmd in ("volume", "vol"):
        if not gid:
            return await reply("❌ No context set.")
        if args:
            try:
                vol = int(args[0])
                await pm.set_volume(gid, vol)
                await reply(f"🔊 Volume set to **{vol}**")
            except ValueError:
                await reply("❌ Volume must be a number (1-1000).")
        else:
            player = pm.client.lavalink.player_manager.get(gid)
            vol = player.volume if player else "?"
            await reply(f"🔊 Current volume: **{vol}**")

    elif cmd == "pause":
        if not gid:
            return await reply("❌ No context set.")
        player = pm.client.lavalink.player_manager.get(gid)
        if not player:
            return await reply("❌ Nothing is playing.")
        await pm.pause(gid)
        state = "⏸ Paused" if player.paused else "▶ Resumed"
        await reply(state)

    elif cmd == "shuffle":
        if not gid:
            return await reply("❌ No context set.")
        await pm.shuffle(gid)
        await reply("🔀 Queue shuffled!")

    elif cmd == "loop":
        if not args:
            return await reply("❌ Usage: `!loop <track/queue/off>`")
        if not gid:
            return await reply("❌ No context set.")
        await pm.loop(gid, args[0])
        await reply(f"🔁 Loop: **{args[0]}**")

    elif cmd == "clear":
        if not gid:
            return await reply("❌ No context set.")
        await pm.clear_queue(gid)
        await reply("🗑 Queue cleared.")

    elif cmd == "replay":
        if not gid:
            return await reply("❌ No context set.")
        await pm.replay(gid)
        await reply("🔄 Current track pushed to queue #1.")

    elif cmd == "filter":
        if not gid:
            return await reply("❌ No context set.")
        if not args:
            return await reply("❌ Usage: `!filter <name>` or `!filter clear`")
        fname = args[0].lower()
        await pm.apply_filter_cmd(gid, fname)
        if fname == "clear":
            await reply("✅ Filters cleared.")
        else:
            await reply(f"✅ Filter applied: **{fname}**")

    elif cmd == "filters":
        filters = list_filters()
        await reply("**Available filters:**\n`" + "` · `".join(filters) + "`")

    elif cmd == "tts":
        if not args:
            return await reply("❌ Usage: `!tts <text>`")
        if not gid or not vid:
            return await reply("❌ Set context first: `!use <guild_id> <vc_id>`")
        text = " ".join(args)
        await pm.tts.speak(gid, vid, text)
        await reply(f"🗣 Speaking: _{text}_")

    else:
        await reply(f"❓ Unknown command `!{cmd}`. Type `!help` for all commands.")


async def start_cli():
    global cli_started, cli_task
    if cli_started:
        return
    cli_started = True
    set_title(f"CYBORG Music | {len(clients)} bot(s)")
    cli = CLI(player_managers, clients)
    cli_task = asyncio.ensure_future(cli.start())


async def run_client(token, index):
    global ready_count, total_clients
    client = discord.Client()
    pm = PlayerManager(client, CONFIG)

    @client.event
    async def on_ready():
        global ready_count
        logger.success(f"Bot #{index + 1} logged in as {client.user}", token=f"...{truncate_token(token)}")
        logger.info(f"Control via Discord messages with prefix '{PREFIX}' (e.g. {PREFIX}help)")
        pm.setup_lavalink()
        clients.append(client)
        player_managers.append(pm)
        ready_count += 1
        if ready_count >= total_clients:
            await start_cli()

    @client.event
    async def on_message(message):
        if message.author.id != client.user.id:
            return
        if not message.content.startswith(PREFIX):
            return
        try:
            await handle_message_command(message, pm)
        except Exception as e:
            logger.error(f"Message command error: {e}")
            try:
                await message.channel.send(f"❌ Error: {e}")
            except Exception:
                pass

    try:
        await client.start(token)
    except discord.errors.LoginFailure:
        logger.error(f"Bot #{index + 1} failed to login: Invalid token")
        remove_invalid_token(token)
        await client.close()
        total_clients = max(total_clients - 1, 0)
        if ready_count >= total_clients and total_clients > 0:
            await start_cli()
        elif total_clients == 0:
            logger.error("All tokens failed. Exiting.")
    except Exception as e:
        logger.error(f"Bot #{index + 1} encountered an error: {e}")
        await client.close()
        total_clients = max(total_clients - 1, 0)
        if ready_count >= total_clients and total_clients > 0:
            await start_cli()
        elif total_clients == 0:
            logger.error("All tokens failed. Exiting.")


def main():
    global total_clients
    import warnings
    import logging as _logging
    warnings.filterwarnings("ignore", message=".*Event loop is closed.*")
    warnings.filterwarnings("ignore", message=".*sys.meta_path.*")
    _logging.getLogger("asyncio").setLevel(_logging.CRITICAL)

    os.system("")
    set_title("CYBORG Music Selfbot")

    tokens = load_tokens()
    total_clients = len(tokens)

    logger.separator()
    logger.info(f"Found {len(tokens)} token(s)")
    for i, t in enumerate(tokens):
        logger.info(f"  Bot #{i + 1}: ...{truncate_token(t)}")
    logger.separator()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    tasks = [run_client(t, i) for i, t in enumerate(tokens)]
    try:
        loop.run_until_complete(asyncio.gather(*tasks))
    except (KeyboardInterrupt, SystemExit):
        logger.warning("Shutting down...")
    finally:
        async def _cleanup():
            for pm in player_managers:
                try:
                    await pm.close()
                except Exception:
                    pass
            for c in clients:
                if hasattr(c, "lavalink"):
                    try:
                        await c.lavalink.close()
                    except Exception:
                        pass
                try:
                    await c.close()
                except Exception:
                    pass
            await asyncio.sleep(0.5)
        try:
            loop.run_until_complete(_cleanup())
        except Exception:
            pass
        pending = asyncio.all_tasks(loop=loop)
        for task in pending:
            task.cancel()
        if pending:
            try:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
        try:
            if hasattr(loop, 'shutdown_asyncgens'):
                loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        try:
            loop.run_until_complete(asyncio.sleep(0.25))
        except Exception:
            pass
        try:
            loop.close()
        except Exception:
            pass
        os._exit(0)


if __name__ == "__main__":
    main()
