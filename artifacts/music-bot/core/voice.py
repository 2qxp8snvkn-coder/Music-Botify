import discord
import lavalink
from lavalink.errors import ClientError


class LavalinkVoiceClient(discord.VoiceProtocol):
    def __init__(self, client, channel):
        self.client = client
        self.channel = channel
        self.guild_id = channel.guild.id
        self._destroyed = False
        self.lavalink = client.lavalink

    async def on_voice_server_update(self, data):
        lavalink_data = {
            "t": "VOICE_SERVER_UPDATE",
            "d": data,
        }
        await self.lavalink.voice_update_handler(lavalink_data)

    async def on_voice_state_update(self, data):
        channel_id = data["channel_id"]

        if not channel_id:
            await self._destroy()
            return

        self.channel = self.client.get_channel(int(channel_id))

        lavalink_data = {
            "t": "VOICE_STATE_UPDATE",
            "d": data,
        }
        await self.lavalink.voice_update_handler(lavalink_data)

    async def connect(self, *, timeout=None, reconnect=True, self_deaf=True, self_mute=False):
        self.lavalink.player_manager.create(guild_id=self.channel.guild.id)
        await self.channel.guild.change_voice_state(channel=self.channel, self_deaf=self_deaf)

    async def disconnect(self, *, force=False):
        player = self.lavalink.player_manager.get(self.channel.guild.id)

        if not force and player and not player.is_connected:
            return

        await self.channel.guild.change_voice_state(channel=None)

        if player:
            player.channel_id = None

        await self._destroy()

    async def _destroy(self):
        self.cleanup()

        if self._destroyed:
            return

        self._destroyed = True

        try:
            await self.lavalink.player_manager.destroy(self.guild_id)
        except ClientError:
            pass
