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


def load_config():
    base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))

    config_path = os.path.join(base_dir, "config.json")
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
    except FileNotFoundError:
        logger.error(f"config.json not found at {config_path}. Place it next to the executable.")
        sys.exit(1)
    except json.JSONDecodeError as e:
        logger.error(f"config.json is invalid JSON: {e}")
        sys.exit(1)

    if "nodes" not in config or not config["nodes"]:
        logger.error("config.json must contain a non-empty 'nodes' list.")
        sys.exit(1)

    return config


CONFIG = load_config()


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
        logger.error("tokens.txt not found. Create it with one token per line.")
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
        logger.error(f"Failed to remove token from tokens.txt: {e}")


clients = []
player_managers = []
ready_count = 0
total_clients = 0
cli_started = False
cli_task = None


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
        pm.setup_lavalink()
        clients.append(client)
        player_managers.append(pm)
        ready_count += 1
        if ready_count >= total_clients:
            await start_cli()

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
