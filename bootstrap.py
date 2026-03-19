"""
bootstrap.py — PHASE one-cell installer and launcher.

Paste the git clone + pip install + %run bootstrap.py into Colab.
This script does everything else automatically:
  1. Validates all required API keys
  2. Loads model_config.yaml
  3. Initialises budget from TOTAL_BUDGET secret
  4. Spawns PLASMA + FLUX nodes + SOLID validators
  5. Connects to Telegram bot
  6. Sends "PHASE online" message
  7. Starts the main event loop

USAGE (in Google Colab)
───────────────────────
Cell 1 — set secrets once (key icon in left sidebar):
  OPENROUTER_API_KEY
  TELEGRAM_BOT_TOKEN
  GITHUB_TOKEN
  TOTAL_BUDGET

Cell 2 — run every time:
  !git clone https://TOKEN@github.com/EXOAI-1/phase /content/phase
  %cd /content/phase
  !pip install -q aiohttp pyyaml python-telegram-bot
  %run bootstrap.py

PLUGINS
───────
To enable optional plugins, add them to PLUGINS list below:
  PLUGINS = ["skynet"]   # loads plugins/skynet/__init__.py

MODEL OVERRIDES
───────────────
Edit model_config.yaml before running, or set env vars:
  os.environ["PHASE_PLASMA_STRATEGIC"] = "openai/gpt-4.1"
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt= "%H:%M:%S",
)
logger = logging.getLogger("bootstrap")

# ── Configuration ─────────────────────────────────────────────────────────────

# Optional plugins to auto-load (add plugin folder names here)
PLUGINS: list[str] = [
    # "skynet",     # military logistics simulation
    # "translator", # language translation
]

# Data directory for state persistence
DATA_DIR = Path(os.environ.get("PHASE_DATA_DIR", "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)


# ── Colab secrets helper ──────────────────────────────────────────────────────

def _get_secret(name: str, env_fallback: str = "") -> str:
    """Try Colab userdata first, then env var, then empty string."""
    try:
        from google.colab import userdata
        val = userdata.get(name)
        if val:
            return val
    except Exception:
        pass
    return os.environ.get(name, env_fallback)


# ── Pre-flight checks ─────────────────────────────────────────────────────────

def _check_requirements() -> list[str]:
    """Returns list of missing requirements."""
    missing = []

    openrouter = _get_secret("OPENROUTER_API_KEY")
    if not openrouter:
        missing.append("OPENROUTER_API_KEY — required for all LLM calls")
    else:
        os.environ["OPENROUTER_API_KEY"] = openrouter

    telegram = _get_secret("TELEGRAM_BOT_TOKEN")
    if not telegram:
        missing.append("TELEGRAM_BOT_TOKEN — required for Telegram bot")
    else:
        os.environ["TELEGRAM_BOT_TOKEN"] = telegram

    github = _get_secret("GITHUB_TOKEN")
    if github:
        os.environ["GITHUB_TOKEN"] = github
    else:
        logger.warning("GITHUB_TOKEN not set — PLASMA self-evolution will be disabled")

    budget = _get_secret("TOTAL_BUDGET", "50")
    os.environ["TOTAL_BUDGET"] = budget

    return missing


def _check_dependencies() -> list[str]:
    missing = []
    for pkg, import_name in [
        ("aiohttp",              "aiohttp"),
        ("pyyaml",               "yaml"),
        ("python-telegram-bot",  "telegram"),
    ]:
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pkg)
    return missing


# ── Main bootstrap ────────────────────────────────────────────────────────────

async def main():
    print("=" * 56)
    print("  ⚡ PHASE — bootstrapping")
    print("=" * 56)

    # 1. Check dependencies
    missing_deps = _check_dependencies()
    if missing_deps:
        print(f"\n❌ Missing packages: {', '.join(missing_deps)}")
        print(f"   Run: pip install {' '.join(missing_deps)}")
        sys.exit(1)

    # 2. Check secrets
    missing_secrets = _check_requirements()
    if missing_secrets:
        print("\n❌ Missing required secrets:")
        for s in missing_secrets:
            print(f"   · {s}")
        print("\nAdd them in Colab: click the 🔑 key icon → Secrets")
        sys.exit(1)

    print("✅ All secrets loaded")

    # 3. Load config
    from config import cfg, reload_config
    config = reload_config()
    print(f"✅ Config loaded — PLASMA strategic: {config.plasma.strategic}")

    # 4. Init budget
    from state import state
    total_budget = float(os.environ.get("TOTAL_BUDGET", "50"))
    await state.init_budget(total_budget)
    print(f"✅ Budget initialised: ${total_budget:.0f}")

    # 5. Load plugins
    from plugin_base import plugin_registry
    for plugin_name in PLUGINS:
        if plugin_registry.load(plugin_name):
            print(f"✅ Plugin loaded: {plugin_name}")
        else:
            print(f"⚠️  Plugin not found: {plugin_name}")

    # 6. Create FLUX nodes
    from flux_coder      import CoderNode
    from flux_researcher import ResearcherNode
    from flux_reviewer   import ReviewerNode
    from flux_architect  import ArchitectNode

    core_nodes = [
        CoderNode(),
        ResearcherNode(),
        ReviewerNode(),
    ]
    print(f"✅ {len(core_nodes)} core FLUX nodes ready")

    # Add plugin nodes
    for plugin in plugin_registry.list_plugins():
        node = plugin_registry.create_node(plugin.name)
        if node:
            core_nodes.append(node)
            print(f"✅ Plugin node: {plugin.name}")

    # 7. Create PLASMA
    from plasma import Plasma

    plasma = Plasma(
        send_telegram = None,   # patched after bot connects
        version       = "1.0.0",
    )

    # 8. Connect Telegram
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    try:
        from telegram.ext import (
            ApplicationBuilder, CommandHandler,
            MessageHandler, filters,
        )

        app = ApplicationBuilder().token(token).build()

        # Store plasma ref for handlers
        app.bot_data["plasma"] = plasma

        async def on_message(update, context):
            text = update.message.text or ""
            if not text:
                return
            # Capture creator's chat_id on first message
            if "creator_chat_id" not in context.bot_data:
                context.bot_data["creator_chat_id"] = update.effective_chat.id
                logger.info("bootstrap: creator registered: %d",
                            update.effective_chat.id)
            plasma_inst = context.bot_data["plasma"]
            result = await plasma_inst.handle_goal(text)
            if result:
                await update.message.reply_text(result[:4000])

        async def on_command(update, context):
            from telegram_phase import handle_phase_command
            text     = update.message.text or ""
            parts    = text.lstrip("/").split(None, 1)
            command  = parts[0].lower()
            args_str = parts[1] if len(parts) > 1 else ""

            # Capture creator's chat_id on first command too
            if "creator_chat_id" not in context.bot_data:
                context.bot_data["creator_chat_id"] = update.effective_chat.id

            plasma_inst = context.bot_data["plasma"]
            send_fn     = lambda msg: update.message.reply_text(
                msg[:4000], parse_mode="Markdown"
            )
            handled = await handle_phase_command(command, args_str, send_fn, plasma_inst)
            if not handled:
                await update.message.reply_text("Unknown command. Try /phase status")

        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
        app.add_handler(CommandHandler("phase", on_command))

        # Wire send_telegram to the bot
        async def _send(msg: str) -> None:
            try:
                chat_id = app.bot_data.get("creator_chat_id")
                if chat_id:
                    await app.bot.send_message(
                        chat_id    = chat_id,
                        text       = msg[:4000],
                        parse_mode = "Markdown",
                    )
            except Exception as exc:
                logger.warning("telegram send failed: %s", exc)

        plasma._send = _send

        print("✅ Telegram bot connected")

    except ImportError:
        print("⚠️  python-telegram-bot not installed — running without Telegram")

        async def _nosend(msg: str) -> None:
            print(f"[PLASMA] {msg}")

        plasma._send = _nosend
        app          = None

    # 9. Start PLASMA + nodes
    await plasma.start(core_nodes)

    print("\n" + "=" * 56)
    print("  ⚡ PHASE is online")
    print(f"  PLASMA v{plasma._version}")
    print(f"  {len(core_nodes)} FLUX nodes active")
    print(f"  3 SOLID validators active")
    print(f"  Budget: ${total_budget:.0f}")
    print("=" * 56)
    print("\nOpen Telegram and send any message to your bot.")
    print("The first message registers you as the creator.")

    # 10. Run forever
    if app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        try:
            await asyncio.Event().wait()   # run until interrupted
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()

    await plasma.stop()
    print("\nPHASE offline.")


if __name__ == "__main__":
    asyncio.run(main())
