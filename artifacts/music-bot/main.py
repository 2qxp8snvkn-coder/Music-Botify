import os
import sys
import json
import asyncio
import discord
from discord.ext import commands
from core.player import PlayerManager
from core.logger import logger
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

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)
pm_map = {}


def get_pm(guild_id):
    return pm_map.get(guild_id)


@bot.event
async def on_ready():
    logger.success(f"Logged in as {bot.user} (ID: {bot.user.id})")
    logger.info(f"Serving {len(bot.guilds)} server(s)")
    logger.info(f"Use {PREFIX}help to see all commands")
    for guild in bot.guilds:
        pm = PlayerManager(bot, CONFIG, guild.id)
        pm.setup_lavalink()
        pm_map[guild.id] = pm
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening, name=f"{PREFIX}play | Music Bot"
    ))


@bot.event
async def on_guild_join(guild):
    pm = PlayerManager(bot, CONFIG, guild.id)
    pm.setup_lavalink()
    pm_map[guild.id] = pm
    logger.info(f"Joined new guild: {guild.name}")


async def ensure_voice(ctx):
    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.send("❌ You need to be in a voice channel first!")
        return None
    return ctx.author.voice.channel


@bot.command(name="help")
async def help_cmd(ctx):
    embed = discord.Embed(title="🎵 CYBORG Music Bot", color=0x7289DA)
    embed.add_field(name="Music", value=(
        "`!play <song>` — play a song\n"
        "`!skip` — skip track\n"
        "`!stop` — stop & clear queue\n"
        "`!dc` — disconnect\n"
        "`!np` — now playing\n"
        "`!queue` — show queue\n"
        "`!volume [1-1000]` — set volume\n"
        "`!pause` — pause/resume\n"
        "`!seek <seconds>` — seek"
    ), inline=False)
    embed.add_field(name="Queue", value=(
        "`!shuffle` — shuffle\n"
        "`!loop <track/queue/off>` — loop\n"
        "`!clear` — clear queue\n"
        "`!replay` — replay current"
    ), inline=False)
    embed.add_field(name="Filters", value=(
        "`!filter <name>` — apply filter\n"
        "`!filter clear` — remove filters\n"
        "`!filters` — list all filters"
    ), inline=False)
    embed.add_field(name="Sources (use with !play)", value=(
        "`sp ` Spotify · `yt ` YouTube · `sc ` SoundCloud\n"
        "`js ` JioSaavn · `am ` Apple Music · `dz ` Deezer\n"
        "Example: `!play sp blinding lights`"
    ), inline=False)
    embed.add_field(name="TTS", value=(
        "`!tts <text>` — speak in voice channel\n"
        "`!ttsvoice <swara/madhur>` — change voice"
    ), inline=False)
    await ctx.send(embed=embed)


@bot.command(name="play", aliases=["p"])
async def play_cmd(ctx, *, query: str = None):
    if not query:
        return await ctx.send("❌ Usage: `!play <song or URL>`")
    channel = await ensure_voice(ctx)
    if not channel:
        return
    pm = get_pm(ctx.guild.id)
    if not pm:
        return await ctx.send("❌ Bot not ready. Try again in a moment.")
    msg = await ctx.send(f"🔍 Searching: **{query}**...")
    await pm.play(ctx.guild.id, channel.id, query, auto_play=True, response_msg=msg, ctx=ctx)


@bot.command(name="skip", aliases=["s"])
async def skip_cmd(ctx):
    pm = get_pm(ctx.guild.id)
    if not pm:
        return
    await pm.skip(ctx.guild.id)
    await ctx.send("⏭ Skipped!")


@bot.command(name="stop")
async def stop_cmd(ctx):
    pm = get_pm(ctx.guild.id)
    if not pm:
        return
    await pm.stop(ctx.guild.id)
    await ctx.send("⏹ Stopped and cleared queue.")


@bot.command(name="dc", aliases=["disconnect", "leave"])
async def dc_cmd(ctx):
    pm = get_pm(ctx.guild.id)
    if not pm:
        return
    await pm.disconnect(ctx.guild.id)
    await ctx.send("👋 Disconnected.")


@bot.command(name="np", aliases=["nowplaying"])
async def np_cmd(ctx):
    pm = get_pm(ctx.guild.id)
    if not pm:
        return
    player = bot.lavalink.player_manager.get(ctx.guild.id)
    if not player or not player.current:
        return await ctx.send("❌ Nothing is playing.")
    t = player.current
    pos = player.position
    dur = t.duration
    mins_pos, secs_pos = divmod(int(pos / 1000), 60)
    mins_dur, secs_dur = divmod(int(dur / 1000), 60)
    bar_fill = int((pos / dur) * 20) if dur > 0 else 0
    bar = "▓" * bar_fill + "░" * (20 - bar_fill)
    state = "⏸ Paused" if player.paused else "▶ Playing"
    filt = pm.active_filter.get(ctx.guild.id, "none")
    loop_modes = {player.LOOP_NONE: "off", player.LOOP_SINGLE: "track", player.LOOP_QUEUE: "queue"}
    loop = loop_modes.get(player.loop, "off")
    embed = discord.Embed(title=t.title, color=0x1DB954)
    embed.add_field(name=state, value=f"`{bar}`\n{mins_pos:02d}:{secs_pos:02d} / {mins_dur:02d}:{mins_dur:02d}", inline=False)
    embed.set_footer(text=f"Vol: {player.volume} · Loop: {loop} · Filter: {filt} · by {t.author}")
    await ctx.send(embed=embed)


@bot.command(name="queue", aliases=["q"])
async def queue_cmd(ctx):
    player = bot.lavalink.player_manager.get(ctx.guild.id)
    if not player:
        return await ctx.send("❌ Nothing is playing.")
    embed = discord.Embed(title="📋 Queue", color=0x7289DA)
    if player.current:
        mins, secs = divmod(int(player.current.duration / 1000), 60)
        embed.add_field(name="▶ Now Playing", value=f"**{player.current.title}** [{mins:02d}:{secs:02d}]", inline=False)
    if player.queue:
        tracks = []
        for i, t in enumerate(player.queue[:10]):
            mins, secs = divmod(int(t.duration / 1000), 60)
            tracks.append(f"`{i+1}.` {t.title} [{mins:02d}:{secs:02d}]")
        if len(player.queue) > 10:
            tracks.append(f"*...and {len(player.queue)-10} more*")
        embed.add_field(name="Up Next", value="\n".join(tracks), inline=False)
    else:
        embed.add_field(name="Up Next", value="Queue is empty", inline=False)
    await ctx.send(embed=embed)


@bot.command(name="volume", aliases=["vol"])
async def volume_cmd(ctx, vol: int = None):
    pm = get_pm(ctx.guild.id)
    if not pm:
        return
    player = bot.lavalink.player_manager.get(ctx.guild.id)
    if vol is None:
        v = player.volume if player else "?"
        return await ctx.send(f"🔊 Current volume: **{v}**")
    if vol < 1 or vol > 1000:
        return await ctx.send("❌ Volume must be between 1 and 1000.")
    await pm.set_volume(ctx.guild.id, vol)
    await ctx.send(f"🔊 Volume set to **{vol}**")


@bot.command(name="pause")
async def pause_cmd(ctx):
    pm = get_pm(ctx.guild.id)
    if not pm:
        return
    player = bot.lavalink.player_manager.get(ctx.guild.id)
    if not player:
        return await ctx.send("❌ Nothing is playing.")
    await pm.pause(ctx.guild.id)
    await ctx.send("⏸ Paused" if player.paused else "▶ Resumed")


@bot.command(name="seek")
async def seek_cmd(ctx, seconds: int = None):
    if seconds is None:
        return await ctx.send("❌ Usage: `!seek <seconds>`")
    pm = get_pm(ctx.guild.id)
    if not pm:
        return
    await pm.seek(ctx.guild.id, seconds)
    mins, secs = divmod(seconds, 60)
    await ctx.send(f"⏩ Seeked to **{mins:02d}:{secs:02d}**")


@bot.command(name="shuffle")
async def shuffle_cmd(ctx):
    pm = get_pm(ctx.guild.id)
    if not pm:
        return
    await pm.shuffle(ctx.guild.id)
    await ctx.send("🔀 Queue shuffled!")


@bot.command(name="loop")
async def loop_cmd(ctx, mode: str = None):
    if not mode:
        return await ctx.send("❌ Usage: `!loop <track/queue/off>`")
    pm = get_pm(ctx.guild.id)
    if not pm:
        return
    await pm.loop(ctx.guild.id, mode)
    await ctx.send(f"🔁 Loop set to: **{mode}**")


@bot.command(name="clear")
async def clear_cmd(ctx):
    pm = get_pm(ctx.guild.id)
    if not pm:
        return
    await pm.clear_queue(ctx.guild.id)
    await ctx.send("🗑 Queue cleared.")


@bot.command(name="replay")
async def replay_cmd(ctx):
    pm = get_pm(ctx.guild.id)
    if not pm:
        return
    await pm.replay(ctx.guild.id)
    await ctx.send("🔄 Current track pushed to queue #1.")


@bot.command(name="filter")
async def filter_cmd(ctx, *, name: str = None):
    if not name:
        return await ctx.send("❌ Usage: `!filter <name>` or `!filter clear`")
    pm = get_pm(ctx.guild.id)
    if not pm:
        return
    await pm.apply_filter_cmd(ctx.guild.id, name.lower())
    if name.lower() == "clear":
        await ctx.send("✅ All filters cleared.")
    else:
        await ctx.send(f"✅ Filter applied: **{name}**")


@bot.command(name="filters")
async def filters_cmd(ctx):
    f_list = list_filters()
    embed = discord.Embed(title="🎛 Available Filters", color=0x7289DA)
    embed.description = " · ".join([f"`{f}`" for f in f_list])
    await ctx.send(embed=embed)


@bot.command(name="tts")
async def tts_cmd(ctx, *, text: str = None):
    if not text:
        return await ctx.send("❌ Usage: `!tts <text>`")
    channel = await ensure_voice(ctx)
    if not channel:
        return
    pm = get_pm(ctx.guild.id)
    if not pm:
        return
    await pm.tts.speak(ctx.guild.id, channel.id, text)
    await ctx.send(f"🗣 Speaking: _{text[:80]}_")


@bot.command(name="ttsvoice")
async def ttsvoice_cmd(ctx, voice: str = None):
    if not voice:
        return await ctx.send("❌ Usage: `!ttsvoice <swara/madhur>`")
    pm = get_pm(ctx.guild.id)
    if not pm:
        return
    if pm.tts.set_voice(ctx.guild.id, voice.lower()):
        await ctx.send(f"✅ TTS voice set to: **{voice.capitalize()}**")
    else:
        await ctx.send("❌ Unknown voice. Available: `swara`, `madhur`")


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing argument. Type `!help` for usage.")
    else:
        logger.error(f"Command error: {error}")
        await ctx.send(f"❌ Error: {error}")


async def keep_alive_server():
    from aiohttp import web

    async def health(request):
        return web.Response(text="🎵 CYBORG Music Bot is alive!", status=200)

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    port = int(os.environ.get("PORT", 8000))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.success(f"Keep-alive server running on port {port}")


async def run_bot(token):
    await asyncio.gather(
        keep_alive_server(),
        bot.start(token),
    )


def main():
    import warnings
    import logging as _logging
    warnings.filterwarnings("ignore")
    _logging.getLogger("asyncio").setLevel(_logging.CRITICAL)

    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        logger.error("DISCORD_BOT_TOKEN environment variable not set.")
        sys.exit(1)

    logger.separator()
    logger.info("Starting CYBORG Music Bot...")
    logger.separator()

    asyncio.run(run_bot(token))


if __name__ == "__main__":
    main()
