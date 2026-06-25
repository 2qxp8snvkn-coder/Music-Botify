import os
import sys
import json
import asyncio
import discord
from discord.ext import commands, tasks
from core.player import PlayerManager
from core.logger import logger
from core.filters import list_filters

# ─── Config ───────────────────────────────────────────────────────────────────

def load_config():
    base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    config_path = os.path.join(base_dir, "config.json")
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
    except FileNotFoundError:
        logger.error(f"config.json not found.")
        sys.exit(1)
    except json.JSONDecodeError as e:
        logger.error(f"config.json invalid: {e}")
        sys.exit(1)
    if "nodes" not in config or not config["nodes"]:
        logger.error("config.json must have a 'nodes' list.")
        sys.exit(1)
    return config

CONFIG = load_config()
PREFIX = "!"
ACCENT  = 0x7289DA
SUCCESS = 0x43B581
ERROR   = 0xF04747
WARNING = 0xFAA61A
MUSIC   = 0x1DB954

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)
pm_map: dict[int, PlayerManager] = {}

# guild_id -> NP message
np_messages: dict[int, discord.Message] = {}
# guild_id -> True if 24/7 mode
always_on: dict[int, bool] = {}
# guild_id -> asyncio task for auto-leave countdown
leave_tasks: dict[int, asyncio.Task] = {}

# ─── Helpers ──────────────────────────────────────────────────────────────────

def get_pm(guild_id: int) -> PlayerManager | None:
    return pm_map.get(guild_id)

def fmt_time(ms: int) -> str:
    s = int(ms / 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def progress_bar(pos: int, dur: int, size: int = 16) -> str:
    filled = int((pos / dur) * size) if dur > 0 else 0
    bar = "▓" * filled + "░" * (size - filled)
    return f"`{bar}`"

def get_thumbnail(track) -> str | None:
    artwork = getattr(track, 'artwork_url', None)
    if artwork:
        return artwork
    ident = getattr(track, 'identifier', None)
    if ident and len(ident) == 11:
        return f"https://img.youtube.com/vi/{ident}/hqdefault.jpg"
    return None

def loop_emoji(player) -> str:
    if player.loop == player.LOOP_SINGLE:
        return "🔂"
    if player.loop == player.LOOP_QUEUE:
        return "🔁"
    return "➡️"

async def cancel_leave_task(guild_id: int):
    task = leave_tasks.pop(guild_id, None)
    if task and not task.done():
        task.cancel()

async def start_leave_countdown(guild_id: int):
    if always_on.get(guild_id):
        return
    await cancel_leave_task(guild_id)

    async def _countdown():
        await asyncio.sleep(60)
        guild = bot.get_guild(guild_id)
        if guild and guild.voice_client:
            pm = get_pm(guild_id)
            if pm:
                await pm.stop(guild_id)
            logger.info(f"Auto-disconnected from {guild.name} (empty VC)")

    leave_tasks[guild_id] = asyncio.create_task(_countdown())

# ─── Music Control Buttons ────────────────────────────────────────────────────

class MusicView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    def _pm(self):
        return get_pm(self.guild_id)

    def _player(self):
        return bot.lavalink.player_manager.get(self.guild_id) if hasattr(bot, 'lavalink') else None

    @discord.ui.button(emoji="⏮", style=discord.ButtonStyle.secondary)
    async def replay_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        pm = self._pm()
        if pm:
            await pm.replay(self.guild_id)
        await interaction.response.send_message("🔄 Replaying current track!", ephemeral=True)

    @discord.ui.button(emoji="⏸", style=discord.ButtonStyle.primary, custom_id="pause_resume")
    async def pause_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        pm = self._pm()
        player = self._player()
        if pm and player:
            await pm.pause(self.guild_id)
            state = "▶ Resumed" if not player.paused else "⏸ Paused"
            await interaction.response.send_message(state, ephemeral=True)
        else:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)

    @discord.ui.button(emoji="⏭", style=discord.ButtonStyle.primary)
    async def skip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        pm = self._pm()
        if pm:
            await pm.skip(self.guild_id)
        await interaction.response.send_message("⏭ Skipped!", ephemeral=True)

    @discord.ui.button(emoji="⏹", style=discord.ButtonStyle.danger)
    async def stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        pm = self._pm()
        if pm:
            await pm.stop(self.guild_id)
        await interaction.response.send_message("⏹ Stopped.", ephemeral=True)

    @discord.ui.button(emoji="🔀", style=discord.ButtonStyle.secondary)
    async def shuffle_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        pm = self._pm()
        if pm:
            await pm.shuffle(self.guild_id)
        await interaction.response.send_message("🔀 Shuffled!", ephemeral=True)

    @discord.ui.button(emoji="🔁", style=discord.ButtonStyle.secondary)
    async def loop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self._player()
        pm = self._pm()
        if not player or not pm:
            return await interaction.response.send_message("Nothing is playing.", ephemeral=True)
        if player.loop == player.LOOP_NONE:
            await pm.loop(self.guild_id, "track")
            await interaction.response.send_message("🔂 Looping track!", ephemeral=True)
        elif player.loop == player.LOOP_SINGLE:
            await pm.loop(self.guild_id, "queue")
            await interaction.response.send_message("🔁 Looping queue!", ephemeral=True)
        else:
            await pm.loop(self.guild_id, "off")
            await interaction.response.send_message("➡️ Loop off.", ephemeral=True)

    @discord.ui.button(emoji="🔉", style=discord.ButtonStyle.secondary)
    async def vol_down_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self._player()
        pm = self._pm()
        if player and pm:
            new_vol = max(10, player.volume - 20)
            await pm.set_volume(self.guild_id, new_vol)
            await interaction.response.send_message(f"🔉 Volume: **{new_vol}**", ephemeral=True)
        else:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)

    @discord.ui.button(emoji="🔊", style=discord.ButtonStyle.secondary)
    async def vol_up_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = self._player()
        pm = self._pm()
        if player and pm:
            new_vol = min(500, player.volume + 20)
            await pm.set_volume(self.guild_id, new_vol)
            await interaction.response.send_message(f"🔊 Volume: **{new_vol}**", ephemeral=True)
        else:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)


class SearchView(discord.ui.View):
    def __init__(self, results, guild_id: int, voice_channel_id: int):
        super().__init__(timeout=30)
        self.results = results
        self.guild_id = guild_id
        self.voice_channel_id = voice_channel_id
        self.chosen = False

        for i in range(min(5, len(results))):
            btn = discord.ui.Button(
                label=str(i + 1),
                style=discord.ButtonStyle.primary,
                custom_id=f"pick_{i}"
            )
            btn.callback = self._make_callback(i)
            self.add_item(btn)

        cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.danger)
        cancel.callback = self.cancel_callback
        self.add_item(cancel)

    def _make_callback(self, index: int):
        async def callback(interaction: discord.Interaction):
            if self.chosen:
                return await interaction.response.send_message("Already picked!", ephemeral=True)
            self.chosen = True
            self.stop()
            track = self.results[index]
            pm = get_pm(self.guild_id)
            if not pm:
                return await interaction.response.send_message("Bot not ready.", ephemeral=True)
            msg = await interaction.response.send_message(f"🔍 Loading **{track.title}**...")
            real_msg = await interaction.original_response()
            player = bot.lavalink.player_manager.create(self.guild_id)
            guild = bot.get_guild(self.guild_id)
            vc = guild.get_channel(self.voice_channel_id)
            await pm._ensure_connection(guild, vc)
            player.add(track=track)
            mins, secs = divmod(int(track.duration / 1000), 60)
            content = f"✅ Queued: **{track.title}** [{mins:02d}:{secs:02d}]"
            await real_msg.edit(content=content)
            if not player.is_playing:
                await player.play()
        return callback

    async def cancel_callback(self, interaction: discord.Interaction):
        self.chosen = True
        self.stop()
        await interaction.response.send_message("Search cancelled.", ephemeral=True)

    async def on_timeout(self):
        self.stop()

# ─── NP Embed Builder ─────────────────────────────────────────────────────────

def build_np_embed(player, pm, guild_id: int) -> discord.Embed:
    track = player.current
    pos   = player.position
    dur   = track.duration

    # time strings
    mins_p, secs_p = divmod(int(pos / 1000), 60)
    mins_d, secs_d = divmod(int(dur / 1000), 60)
    pos_str = f"{mins_p:02d}:{secs_p:02d}"
    dur_str = f"{mins_d:02d}:{secs_d:02d}"

    # working progress bar — fills based on actual playback position
    BAR_LEN = 16
    filled  = int((pos / dur) * BAR_LEN) if dur > 0 else 0
    dot_pos = max(0, min(filled, BAR_LEN - 1))
    bar_chars = ["▬"] * BAR_LEN
    bar_chars[dot_pos] = "🔘"
    bar = "".join(bar_chars)

    state  = "⏸ Paused" if player.paused else "▶ Playing"
    loop   = loop_emoji(player)
    filt   = pm.active_filter.get(guild_id, "none")
    q_len  = len(player.queue)
    thumb  = get_thumbnail(track)

    uri   = getattr(track, 'uri', None)
    embed = discord.Embed(color=MUSIC)
    embed.set_author(name="🎵  Now Playing")
    embed.title = track.title[:256]
    if uri:
        embed.url = uri
    embed.description = (
        f"by **{track.author}**\n\n"
        f"`{pos_str}` {bar} `{dur_str}`"
    )
    embed.add_field(name="Status",   value=state,              inline=True)
    embed.add_field(name="Loop",     value=loop,               inline=True)
    embed.add_field(name="Volume",   value=f"🔊 {player.volume}", inline=True)
    embed.add_field(name="Filter",   value=f"🎛 {filt}",       inline=True)
    embed.add_field(name="In Queue", value=f"📋 {q_len}",      inline=True)

    if thumb:
        embed.set_thumbnail(url=thumb)

    embed.set_footer(text="Buttons update every 15s  •  CYBORG Music")
    return embed

# ─── Events ───────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    logger.success(f"Logged in as {bot.user} (ID: {bot.user.id})")
    logger.info(f"Serving {len(bot.guilds)} server(s) | Prefix: {PREFIX}")
    for guild in bot.guilds:
        pm = PlayerManager(bot, CONFIG, guild.id)
        pm.setup_lavalink()
        pm_map[guild.id] = pm
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening, name=f"!play | Music Bot"
    ))
    auto_np_update.start()

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    # Normalize smart/curly apostrophes & quotes so the parser never chokes on them
    message.content = (
        message.content
        .replace('\u2018', "'").replace('\u2019', "'")   # '' → '
        .replace('\u201c', '"').replace('\u201d', '"')   # "" → "
        .replace('\u2032', "'")                          # ′ → '
    )
    await bot.process_commands(message)

@bot.event
async def on_guild_join(guild: discord.Guild):
    pm = PlayerManager(bot, CONFIG, guild.id)
    pm.setup_lavalink()
    pm_map[guild.id] = pm
    logger.info(f"Joined: {guild.name}")

@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if member.bot:
        return
    guild = member.guild
    vc = guild.voice_client
    if not vc:
        return
    # Check if VC is empty (only the bot left)
    members = [m for m in vc.channel.members if not m.bot]
    if len(members) == 0:
        await start_leave_countdown(guild.id)
    else:
        await cancel_leave_task(guild.id)

# ─── Auto NP Update ───────────────────────────────────────────────────────────

@tasks.loop(seconds=15)
async def auto_np_update():
    for guild_id, msg in list(np_messages.items()):
        try:
            player = bot.lavalink.player_manager.get(guild_id)
            pm = get_pm(guild_id)
            if not player or not player.current or not pm:
                continue
            embed = build_np_embed(player, pm, guild_id)
            await msg.edit(embed=embed)
        except Exception:
            np_messages.pop(guild_id, None)

@auto_np_update.before_loop
async def before_auto_np():
    await bot.wait_until_ready()

# ─── Music Commands ───────────────────────────────────────────────────────────

async def ensure_voice(ctx) -> discord.VoiceChannel | None:
    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.send(embed=discord.Embed(
            description="❌ You need to be in a voice channel first!",
            color=ERROR
        ))
        return None
    return ctx.author.voice.channel

@bot.command(name="join", aliases=["j"])
async def join_cmd(ctx):
    channel = await ensure_voice(ctx)
    if not channel:
        return
    pm = get_pm(ctx.guild.id)
    if not pm:
        return
    guild = ctx.guild
    if guild.voice_client:
        return await ctx.send(embed=discord.Embed(description=f"Already in **{guild.voice_client.channel.name}**!", color=WARNING))
    bot.lavalink.player_manager.create(ctx.guild.id)
    from core.voice import LavalinkVoiceClient
    await channel.connect(cls=LavalinkVoiceClient)
    await ctx.send(embed=discord.Embed(description=f"✅ Joined **{channel.name}**!", color=SUCCESS))

@bot.command(name="play", aliases=["p"])
async def play_cmd(ctx, *, query: str = None):
    if not query:
        return await ctx.send(embed=discord.Embed(description="❌ Usage: `!play <song or URL>`", color=ERROR))
    channel = await ensure_voice(ctx)
    if not channel:
        return
    pm = get_pm(ctx.guild.id)
    if not pm:
        return await ctx.send(embed=discord.Embed(description="❌ Bot not ready.", color=ERROR))
    msg = await ctx.send(embed=discord.Embed(description=f"🔍 Searching: **{query}**...", color=ACCENT))
    await pm.play(ctx.guild.id, channel.id, query, auto_play=True, response_msg=msg, ctx=ctx)

@bot.command(name="search", aliases=["find"])
async def search_cmd(ctx, *, query: str = None):
    if not query:
        return await ctx.send(embed=discord.Embed(description="❌ Usage: `!search <song name>`", color=ERROR))
    channel = await ensure_voice(ctx)
    if not channel:
        return
    pm = get_pm(ctx.guild.id)
    if not pm:
        return

    msg = await ctx.send(embed=discord.Embed(description=f"🔍 Searching for **{query}**...", color=ACCENT))
    player = bot.lavalink.player_manager.create(ctx.guild.id)
    results = await player.node.get_tracks(f"ytsearch:{query}")

    if not results or not results.tracks:
        return await msg.edit(embed=discord.Embed(description="❌ No results found.", color=ERROR))

    tracks = results.tracks[:5]
    embed = discord.Embed(title=f"🔎 Search Results for: {query}", color=ACCENT)
    for i, t in enumerate(tracks):
        mins, secs = divmod(int(t.duration / 1000), 60)
        embed.add_field(
            name=f"{i+1}. {t.title[:50]}",
            value=f"*{t.author}* · {mins:02d}:{secs:02d}",
            inline=False
        )
    embed.set_footer(text="Pick a result using the buttons below (30s timeout)")
    view = SearchView(tracks, ctx.guild.id, channel.id)
    await msg.edit(embed=embed, view=view)

@bot.command(name="skip", aliases=["s"])
async def skip_cmd(ctx):
    pm = get_pm(ctx.guild.id)
    if not pm:
        return
    player = bot.lavalink.player_manager.get(ctx.guild.id)
    if not player or not player.is_playing:
        return await ctx.send(embed=discord.Embed(description="❌ Nothing is playing.", color=ERROR))
    title = player.current.title if player.current else "track"
    await pm.skip(ctx.guild.id)
    await ctx.send(embed=discord.Embed(description=f"⏭ Skipped **{title}**", color=SUCCESS))

@bot.command(name="stop")
async def stop_cmd(ctx):
    pm = get_pm(ctx.guild.id)
    if not pm:
        return
    await pm.stop(ctx.guild.id)
    np_messages.pop(ctx.guild.id, None)
    await ctx.send(embed=discord.Embed(description="⏹ Stopped and cleared the queue.", color=SUCCESS))

@bot.command(name="dc", aliases=["disconnect", "leave"])
async def dc_cmd(ctx):
    pm = get_pm(ctx.guild.id)
    if not pm:
        return
    if not ctx.guild.voice_client:
        return await ctx.send(embed=discord.Embed(description="❌ Not connected to any voice channel.", color=ERROR))
    await pm.disconnect(ctx.guild.id)
    np_messages.pop(ctx.guild.id, None)
    await cancel_leave_task(ctx.guild.id)
    await ctx.send(embed=discord.Embed(description="👋 Disconnected.", color=SUCCESS))

@bot.command(name="np", aliases=["nowplaying", "current"])
async def np_cmd(ctx):
    pm = get_pm(ctx.guild.id)
    if not pm:
        return
    player = bot.lavalink.player_manager.get(ctx.guild.id)
    if not player or not player.current:
        return await ctx.send(embed=discord.Embed(description="❌ Nothing is playing.", color=ERROR))
    embed = build_np_embed(player, pm, ctx.guild.id)
    view = MusicView(ctx.guild.id)
    msg = await ctx.send(embed=embed, view=view)
    np_messages[ctx.guild.id] = msg

@bot.command(name="queue", aliases=["q"])
async def queue_cmd(ctx, page: int = 1):
    player = bot.lavalink.player_manager.get(ctx.guild.id)
    if not player:
        return await ctx.send(embed=discord.Embed(description="❌ Nothing is playing.", color=ERROR))

    per_page = 10
    total = len(player.queue)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    end = start + per_page

    embed = discord.Embed(title="📋 Queue", color=ACCENT)

    if player.current:
        mins, secs = divmod(int(player.current.duration / 1000), 60)
        embed.add_field(
            name="▶ Now Playing",
            value=f"**{player.current.title}** `{mins:02d}:{secs:02d}`\n*{player.current.author}*",
            inline=False
        )

    if player.queue:
        lines = []
        for i, t in enumerate(list(player.queue)[start:end], start=start + 1):
            mins, secs = divmod(int(t.duration / 1000), 60)
            lines.append(f"`{i}.` **{t.title[:45]}** `{mins:02d}:{secs:02d}`")
        embed.add_field(name="Up Next", value="\n".join(lines) or "Empty", inline=False)

        total_dur = sum(t.duration for t in player.queue)
        h, rem = divmod(int(total_dur / 1000), 3600)
        m, s = divmod(rem, 60)
        dur_str = f"{h}h {m}m" if h else f"{m}m {s}s"
        embed.set_footer(text=f"Page {page}/{total_pages} · {total} tracks · Total: {dur_str}")
    else:
        embed.add_field(name="Up Next", value="Queue is empty", inline=False)

    await ctx.send(embed=embed)

@bot.command(name="remove", aliases=["rm"])
async def remove_cmd(ctx, pos: int = None):
    if pos is None:
        return await ctx.send(embed=discord.Embed(description="❌ Usage: `!remove <position>`", color=ERROR))
    player = bot.lavalink.player_manager.get(ctx.guild.id)
    if not player or not player.queue:
        return await ctx.send(embed=discord.Embed(description="❌ Queue is empty.", color=ERROR))
    if pos < 1 or pos > len(player.queue):
        return await ctx.send(embed=discord.Embed(description=f"❌ Position must be 1-{len(player.queue)}", color=ERROR))
    track = player.queue[pos - 1]
    del player.queue[pos - 1]
    await ctx.send(embed=discord.Embed(description=f"🗑 Removed **{track.title}** from queue.", color=SUCCESS))

@bot.command(name="move", aliases=["mv"])
async def move_cmd(ctx, frm: int = None, to: int = None):
    if not frm or not to:
        return await ctx.send(embed=discord.Embed(description="❌ Usage: `!move <from> <to>`", color=ERROR))
    player = bot.lavalink.player_manager.get(ctx.guild.id)
    if not player or not player.queue:
        return await ctx.send(embed=discord.Embed(description="❌ Queue is empty.", color=ERROR))
    n = len(player.queue)
    if not (1 <= frm <= n and 1 <= to <= n):
        return await ctx.send(embed=discord.Embed(description=f"❌ Positions must be between 1 and {n}", color=ERROR))
    track = player.queue.pop(frm - 1)
    player.queue.insert(to - 1, track)
    await ctx.send(embed=discord.Embed(description=f"✅ Moved **{track.title}** to position **{to}**", color=SUCCESS))

@bot.command(name="volume", aliases=["vol", "v"])
async def volume_cmd(ctx, vol: int = None):
    pm = get_pm(ctx.guild.id)
    player = bot.lavalink.player_manager.get(ctx.guild.id) if hasattr(bot, 'lavalink') else None
    if not pm or not player:
        return await ctx.send(embed=discord.Embed(description="❌ Nothing is playing.", color=ERROR))
    if vol is None:
        bar = "🔊 " + "█" * (player.volume // 20) + "░" * (50 - player.volume // 20)
        return await ctx.send(embed=discord.Embed(description=f"Volume: **{player.volume}**\n{bar[:40]}", color=ACCENT))
    if not 1 <= vol <= 1000:
        return await ctx.send(embed=discord.Embed(description="❌ Volume must be between 1 and 1000.", color=ERROR))
    await pm.set_volume(ctx.guild.id, vol)
    await ctx.send(embed=discord.Embed(description=f"🔊 Volume set to **{vol}**", color=SUCCESS))

@bot.command(name="pause", aliases=["resume"])
async def pause_cmd(ctx):
    pm = get_pm(ctx.guild.id)
    player = bot.lavalink.player_manager.get(ctx.guild.id) if hasattr(bot, 'lavalink') else None
    if not pm or not player:
        return await ctx.send(embed=discord.Embed(description="❌ Nothing is playing.", color=ERROR))
    await pm.pause(ctx.guild.id)
    state = "⏸ Paused" if player.paused else "▶ Resumed"
    await ctx.send(embed=discord.Embed(description=state, color=SUCCESS))

@bot.command(name="seek")
async def seek_cmd(ctx, seconds: int = None):
    if seconds is None:
        return await ctx.send(embed=discord.Embed(description="❌ Usage: `!seek <seconds>`", color=ERROR))
    pm = get_pm(ctx.guild.id)
    if not pm:
        return
    await pm.seek(ctx.guild.id, seconds)
    m, s = divmod(seconds, 60)
    await ctx.send(embed=discord.Embed(description=f"⏩ Seeked to **{m:02d}:{s:02d}**", color=SUCCESS))

@bot.command(name="shuffle")
async def shuffle_cmd(ctx):
    pm = get_pm(ctx.guild.id)
    player = bot.lavalink.player_manager.get(ctx.guild.id) if hasattr(bot, 'lavalink') else None
    if not pm or not player:
        return
    if len(player.queue) < 2:
        return await ctx.send(embed=discord.Embed(description="❌ Need at least 2 tracks in queue to shuffle.", color=ERROR))
    await pm.shuffle(ctx.guild.id)
    await ctx.send(embed=discord.Embed(description=f"🔀 Shuffled **{len(player.queue)}** tracks!", color=SUCCESS))

@bot.command(name="loop", aliases=["repeat"])
async def loop_cmd(ctx, mode: str = None):
    if not mode or mode not in ("track", "queue", "off"):
        return await ctx.send(embed=discord.Embed(description="❌ Usage: `!loop <track | queue | off>`", color=ERROR))
    pm = get_pm(ctx.guild.id)
    if not pm:
        return
    await pm.loop(ctx.guild.id, mode)
    icons = {"track": "🔂 Looping current track", "queue": "🔁 Looping entire queue", "off": "➡️ Loop disabled"}
    await ctx.send(embed=discord.Embed(description=icons[mode], color=SUCCESS))

@bot.command(name="clear")
async def clear_cmd(ctx):
    pm = get_pm(ctx.guild.id)
    player = bot.lavalink.player_manager.get(ctx.guild.id) if hasattr(bot, 'lavalink') else None
    if not pm or not player:
        return
    count = len(player.queue)
    await pm.clear_queue(ctx.guild.id)
    await ctx.send(embed=discord.Embed(description=f"🗑 Cleared **{count}** track(s) from the queue.", color=SUCCESS))

@bot.command(name="replay", aliases=["again"])
async def replay_cmd(ctx):
    pm = get_pm(ctx.guild.id)
    player = bot.lavalink.player_manager.get(ctx.guild.id) if hasattr(bot, 'lavalink') else None
    if not pm or not player or not player.current:
        return await ctx.send(embed=discord.Embed(description="❌ Nothing is playing.", color=ERROR))
    await pm.replay(ctx.guild.id)
    await ctx.send(embed=discord.Embed(description=f"🔄 **{player.current.title}** added back to queue #1.", color=SUCCESS))

@bot.command(name="247", aliases=["always"])
async def always_on_cmd(ctx):
    gid = ctx.guild.id
    always_on[gid] = not always_on.get(gid, False)
    if always_on[gid]:
        await cancel_leave_task(gid)
        await ctx.send(embed=discord.Embed(description="🌙 **24/7 mode ON** — I'll stay in the channel forever!", color=SUCCESS))
    else:
        await ctx.send(embed=discord.Embed(description="🌙 **24/7 mode OFF** — I'll leave after 60s of inactivity.", color=WARNING))

# ─── Filter Commands ──────────────────────────────────────────────────────────

@bot.command(name="filter", aliases=["fx"])
async def filter_cmd(ctx, *, name: str = None):
    if not name:
        return await ctx.send(embed=discord.Embed(description="❌ Usage: `!filter <name>` or `!filter clear`", color=ERROR))
    pm = get_pm(ctx.guild.id)
    if not pm:
        return
    await pm.apply_filter_cmd(ctx.guild.id, name.lower())
    if name.lower() == "clear":
        await ctx.send(embed=discord.Embed(description="✅ All filters cleared.", color=SUCCESS))
    else:
        await ctx.send(embed=discord.Embed(description=f"🎛 Filter applied: **{name}**", color=SUCCESS))

@bot.command(name="filters")
async def filters_cmd(ctx):
    f_list = list_filters()
    embed = discord.Embed(title="🎛 Available Filters", color=ACCENT)
    descriptions = {
        "lofi": "Chill slowed vibe", "nightcore": "Speed + pitch up",
        "slowmo": "Slowed + pitched down", "chipmunk": "High-pitch fast",
        "darthvader": "Deep dark voice", "daycore": "Slightly slower",
        "damon": "Ultra slowed", "8d": "3D rotating audio",
        "tremolo": "Wavering effect", "vibrate": "Tremolo + vibrato",
        "bassboost": "Enhanced bass", "earrape": "Max distortion",
        "121": "Custom FX", "dis": "Distorted", "loud": "Maximum loud"
    }
    rows = [f"`{f}` — {descriptions.get(f, '')}" for f in f_list]
    embed.description = "\n".join(rows)
    embed.set_footer(text="Use !filter <name> to apply · !filter clear to remove")
    await ctx.send(embed=embed)

# ─── TTS Commands ─────────────────────────────────────────────────────────────

@bot.command(name="tts")
async def tts_cmd(ctx, *, text: str = None):
    # Fall back to raw message content in case argument parsing failed on apostrophes
    if not text:
        raw = ctx.message.content
        parts = raw.split(None, 1)
        text = parts[1] if len(parts) > 1 else None
    if not text:
        return await ctx.send(embed=discord.Embed(description="❌ Usage: `!tts <text>`", color=ERROR))
    channel = await ensure_voice(ctx)
    if not channel:
        return
    pm = get_pm(ctx.guild.id)
    if not pm:
        return
    await ctx.send(embed=discord.Embed(description=f"🗣 Speaking: *{text[:80]}*", color=ACCENT))
    await pm.tts.speak(ctx.guild.id, channel.id, text)

@bot.command(name="ttsvoice")
async def ttsvoice_cmd(ctx, voice: str = None):
    if not voice:
        return await ctx.send(embed=discord.Embed(description="❌ Usage: `!ttsvoice <swara|madhur>`", color=ERROR))
    pm = get_pm(ctx.guild.id)
    if not pm:
        return
    if pm.tts.set_voice(ctx.guild.id, voice.lower()):
        await ctx.send(embed=discord.Embed(description=f"✅ TTS voice: **{voice.capitalize()}**", color=SUCCESS))
    else:
        await ctx.send(embed=discord.Embed(description="❌ Unknown voice. Use: `swara` or `madhur`", color=ERROR))

# ─── Help Command ─────────────────────────────────────────────────────────────

@bot.command(name="help", aliases=["h", "commands"])
async def help_cmd(ctx):
    embed = discord.Embed(
        title="🎵 CYBORG Music Bot — Commands",
        description=f"Prefix: `{PREFIX}` · Join a VC before using music commands",
        color=ACCENT
    )
    embed.add_field(name="🎵 Playback", value=(
        "`!play <song/url>` · `!search <song>`\n"
        "`!skip` · `!stop` · `!pause` · `!seek <s>`\n"
        "`!join` · `!dc` · `!np`"
    ), inline=False)
    embed.add_field(name="📋 Queue", value=(
        "`!queue [page]` · `!clear`\n"
        "`!remove <pos>` · `!move <from> <to>`\n"
        "`!shuffle` · `!loop <track/queue/off>` · `!replay`"
    ), inline=False)
    embed.add_field(name="🎛 Filters", value=(
        "`!filter <name>` · `!filter clear` · `!filters`\n"
        "nightcore, lofi, bassboost, 8d, slowmo, earrape + more"
    ), inline=False)
    embed.add_field(name="🔊 Volume", value="`!volume [1-1000]`", inline=True)
    embed.add_field(name="🌙 24/7 Mode", value="`!247`", inline=True)
    embed.add_field(name="🗣 TTS", value="`!tts <text>` · `!ttsvoice <voice>`", inline=False)
    embed.add_field(name="🎯 Sources", value=(
        "`sp ` Spotify · `yt ` YouTube · `sc ` SoundCloud\n"
        "`js ` JioSaavn · `am ` Apple · `dz ` Deezer\n"
        "Example: `!play sp blinding lights`"
    ), inline=False)
    embed.set_footer(text="Use !np to get an interactive control panel with buttons!")
    await ctx.send(embed=embed)

# ─── Error Handler ────────────────────────────────────────────────────────────

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(embed=discord.Embed(description=f"❌ Missing argument. Type `!help` for usage.", color=ERROR))
    elif isinstance(error, commands.BadArgument):
        await ctx.send(embed=discord.Embed(description=f"❌ Invalid argument. Type `!help` for usage.", color=ERROR))
    elif isinstance(error, (commands.UnexpectedQuoteError, commands.InvalidEndOfQuotedStringError, commands.ExpectedClosingQuoteError)):
        # Re-invoke the command treating the raw content as a plain string (ignores quote parsing)
        raw = ctx.message.content
        prefix_len = len(ctx.prefix or PREFIX)
        cmd_name = ctx.invoked_with or ""
        raw_args = raw[prefix_len + len(cmd_name):].strip()
        if ctx.command and raw_args:
            try:
                await ctx.command.callback(ctx, **{ctx.command.clean_params and list(ctx.command.clean_params.keys())[0] or "text": raw_args})
                return
            except Exception:
                pass
        await ctx.send(embed=discord.Embed(
            description=f"❌ Your message has special characters (like `'` or `\"`) that caused an error. Try rephrasing without them.",
            color=ERROR
        ))
    else:
        logger.error(f"Command error in {ctx.command}: {error}")
        await ctx.send(embed=discord.Embed(description=f"❌ An error occurred: `{error}`", color=ERROR))

# ─── Keep-Alive & Main ────────────────────────────────────────────────────────

TTS_SERVE_DIR = "/tmp/cyborg_tts"
os.makedirs(TTS_SERVE_DIR, exist_ok=True)

async def keep_alive_server():
    from aiohttp import web

    async def health(request):
        guilds = len(bot.guilds)
        playing = sum(
            1 for g in bot.guilds
            if g.voice_client and hasattr(bot, 'lavalink')
            and bot.lavalink.player_manager.get(g.id)
            and bot.lavalink.player_manager.get(g.id).is_playing
        )
        return web.Response(
            text=f"🎵 CYBORG Music Bot\nServers: {guilds}\nCurrently playing: {playing}",
            status=200
        )

    async def serve_tts(request):
        filename = request.match_info.get("filename", "")
        filepath = os.path.join(TTS_SERVE_DIR, filename)
        if not os.path.isfile(filepath) or not filename.endswith(".mp3"):
            return web.Response(status=404, text="Not found")
        return web.FileResponse(filepath, headers={"Content-Type": "audio/mpeg"})

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_get("/tts/{filename}", serve_tts)

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
    import warnings, logging as _logging
    warnings.filterwarnings("ignore")
    _logging.getLogger("asyncio").setLevel(_logging.CRITICAL)

    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        logger.error("DISCORD_BOT_TOKEN not set.")
        sys.exit(1)

    logger.separator()
    logger.info("Starting CYBORG Music Bot (Upgraded)...")
    logger.separator()
    asyncio.run(run_bot(token))


if __name__ == "__main__":
    main()
