"""
phase/telegram_phase.py — Telegram command handlers for PHASE.

All commands go through PLASMA. The user never talks directly
to a FLUX node or SOLID — only to PLASMA.

Add to your Telegram dispatcher:
    from telegram_phase import handle_phase_command
    handled = await handle_phase_command(command, args, send_fn, plasma)
    if handled:
        return

COMMANDS
--------
  /phase status        — PLASMA + nodes + SOLID + budget
  /phase nodes         — list active FLUX nodes
  /phase evolve        — trigger PLASMA self-evolution cycle
  /phase senate        — last 10 SOLID votes
  /phase tasks         — current task queue
  /phase history [N]   — last N events (default 15)
  /phase plugin list   — loaded plugins
  /phase plugin load <name>  — load a plugin at runtime
  /phase config        — show current model config
"""

from __future__ import annotations

import os
import time
from typing import Callable, Awaitable

SendFn = Callable[[str], Awaitable[None]]


async def handle_phase_command(
    command: str,
    args:    str,
    send:    SendFn,
    plasma,              # Plasma instance
) -> bool:
    """Returns True if command was handled."""
    cmd = command.lower().strip()

    if cmd == "phase":
        sub      = args.strip().split()[0].lower() if args.strip() else "status"
        sub_args = " ".join(args.strip().split()[1:])

        dispatch = {
            "status":  _cmd_status,
            "nodes":   _cmd_nodes,
            "evolve":  _cmd_evolve,
            "senate":  _cmd_senate,
            "tasks":   _cmd_tasks,
            "history": _cmd_history,
            "plugin":  _cmd_plugin,
            "config":  _cmd_config,
        }

        handler = dispatch.get(sub)
        if handler:
            await handler(sub_args, send, plasma)
        else:
            await _cmd_help(send)
        return True

    return False


async def _cmd_status(args, send, plasma):
    msg = await plasma.handle_phase_status()
    await send(msg)


async def _cmd_nodes(args, send, plasma):
    from state import state
    nodes = state.get_nodes()
    if not nodes:
        await send("No FLUX nodes active.")
        return

    lines = ["🔵 *Active FLUX nodes*\n"]
    for n in nodes:
        status_icon = {"idle": "⚪", "busy": "🟢", "error": "🔴", "stopped": "⛔"}.get(
            n.get("status", "?"), "❓"
        )
        lines.append(
            f"{status_icon} *{n.get('node_type','?')}* [{n.get('node_id','?')[-6:]}]\n"
            f"  Model: `{n.get('model','?')}`\n"
            f"  Tasks done: {n.get('tasks_done', 0)} | "
            f"Spend: ${n.get('total_spend', 0):.4f}"
        )
    await send("\n\n".join(lines))


async def _cmd_evolve(args, send, plasma):
    result = await plasma.handle_evolve_command()
    await send(result)


async def _cmd_senate(args, send, plasma):
    from state import state
    events = [
        e for e in state.recent_events(50)
        if e.get("source") == "SOLID"
    ][-10:]

    if not events:
        await send("No SOLID votes recorded yet.")
        return

    lines = ["⚖️ *Recent SOLID votes*\n"]
    for e in reversed(events):
        ts   = time.strftime("%H:%M", time.localtime(e.get("timestamp", 0)))
        icon = "✅" if "APPROVED" in e.get("message", "") else "❌"
        lines.append(f"{icon} [{ts}] {e.get('message', '')}")

    summary = state.summary()["solid"]
    lines.append(
        f"\nApproval rate: {summary['approval_rate']}% "
        f"({summary['approved']}/{summary['total']} votes)"
    )
    await send("\n".join(lines))


async def _cmd_tasks(args, send, plasma):
    from task import task_queue, TaskStatus

    all_tasks = task_queue.all_tasks()
    active    = [t for t in all_tasks if not t.is_terminal()]
    recent    = sorted(
        [t for t in all_tasks if t.is_terminal()],
        key=lambda t: t.completed_at or 0, reverse=True
    )[:5]

    lines = [f"📋 *Task queue*  ({len(active)} active)\n"]

    if active:
        for t in active:
            lines.append(
                f"⏳ [{t.task_id}] {t.node_type}: {t.goal[:50]}…\n"
                f"   Status: {t.status.value} | Attempt: {t.attempts}"
            )
    else:
        lines.append("No active tasks.\n")

    if recent:
        lines.append("\n*Recent completed:*")
        for t in recent:
            icon = "✅" if t.status.value == "done" else "❌"
            lines.append(f"{icon} [{t.task_id}] {t.node_type}: {t.goal[:50]}…")

    await send("\n".join(lines)[:3500])


async def _cmd_history(args, send, plasma):
    from state import state
    try:
        n = int(args.strip()) if args.strip() else 15
        n = max(1, min(50, n))
    except ValueError:
        n = 15

    events = state.recent_events(n)
    if not events:
        await send("No events recorded yet.")
        return

    import time as t_mod
    lines = [f"📜 *Last {n} events*\n"]
    for e in reversed(events):
        ts   = t_mod.strftime("%H:%M:%S", t_mod.localtime(e.get("timestamp", 0)))
        src  = e.get("source", "?")
        etype= e.get("event_type", "?")
        msg  = e.get("message", "")
        lines.append(f"[{ts}] *{src}* {etype}\n  {msg}")

    await send("\n".join(lines)[:3500])


async def _cmd_plugin(args, send, plasma):
    from plugin_base import plugin_registry

    parts  = args.strip().split()
    sub    = parts[0].lower() if parts else "list"
    p_name = parts[1] if len(parts) > 1 else ""

    if sub == "list":
        await send(f"🔌 *Plugins*\n\n{plugin_registry.summary()}")

    elif sub == "load" and p_name:
        success = plugin_registry.load(p_name)
        if success:
            plugin = plugin_registry.get(p_name)
            node   = plugin_registry.create_node(p_name)
            if node:
                await node.start()
                plasma._nodes[node.node_id] = node
                await send(f"✅ Plugin '{p_name}' loaded and node started.")
            else:
                await send(f"✅ Plugin '{p_name}' loaded but node creation failed.")
        else:
            await send(f"❌ Failed to load plugin '{p_name}'. Check the plugins/ directory.")

    elif sub == "unload" and p_name:
        await send(f"Plugin unloading not yet implemented. Restart PHASE to unload.")

    else:
        await send(
            "Plugin commands:\n"
            "  /phase plugin list\n"
            "  /phase plugin load <name>\n"
        )


async def _cmd_config(args, send, plasma):
    from config import cfg
    msg = (
        f"⚙️ *PHASE model config*\n\n"
        f"*PLASMA*\n"
        f"  Strategic:    `{cfg.plasma.strategic}`\n"
        f"  Coordination: `{cfg.plasma.coordination}`\n"
        f"  Light:        `{cfg.plasma.light}`\n\n"
        f"*SOLID validators*\n"
        f"  v1: `{cfg.solid.validator_1}`\n"
        f"  v2: `{cfg.solid.validator_2}`\n"
        f"  v3: `{cfg.solid.validator_3}`\n\n"
        f"*FLUX nodes*\n"
        f"  coder:      `{cfg.flux_model('coder')}`\n"
        f"  researcher: `{cfg.flux_model('researcher')}`\n"
        f"  reviewer:   `{cfg.flux_model('reviewer')}`\n"
        f"  architect:  `{cfg.flux_model('architect')}`\n\n"
        f"Edit model_config.yaml to change any model."
    )
    await send(msg)


async def _cmd_help(send):
    await send(
        "⚡ *PHASE commands*\n\n"
        "`/phase status`   — full system status\n"
        "`/phase nodes`    — active FLUX nodes\n"
        "`/phase evolve`   — trigger PLASMA evolution\n"
        "`/phase senate`   — recent SOLID votes\n"
        "`/phase tasks`    — task queue\n"
        "`/phase history [N]` — last N events\n"
        "`/phase plugin list` — loaded plugins\n"
        "`/phase plugin load <name>` — load plugin\n"
        "`/phase config`   — model configuration\n\n"
        "Just type your goal to set PLASMA in motion."
    )
