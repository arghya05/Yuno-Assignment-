# Workflow templates

A **template** is a named workflow defined over a set of agents: an entry
point (cron or channel), the agents that participate, and the routing
conditions (including feedback loops) that connect them.

Agents are the reusable building blocks (`agents/*.md`); templates compose
them into a workflow. Two are pre-built:

| Template | Entry | Agents | Channel |
|---|---|---|---|
| **fraud_monitor** | `fraud_screener` on cron (hourly) | fraud_screener → concierge → investigator / resolver → concierge | Telegram |
| **payment_dispute** | `concierge` on Telegram (human-initiated) | concierge → investigator → concierge → resolver → concierge | Telegram |

Each lives in its own folder as a `template.yaml` manifest describing the
entry point, the participating agents, and the conditioned flow.

## How a template maps onto the running system

The runtime auto-discovers every `agents/*.md` at startup and wires their
`dispatches_to:` conditions into the dispatch bus. A template manifest is the
human-readable description of one workflow expressed over those agents — it
documents *which* agents collaborate and *under what conditions*, mirroring
the `dispatches_to:` blocks in the SOUL.md files themselves.

## Adding a new template

1. Create `templates/<name>/template.yaml` with `entry`, `agents`, and `flow`.
2. Make sure each listed agent exists as `agents/<agent>.md`. If a template
   needs a new agent, drop a new SOUL.md into `agents/` — the runtime
   discovers it on restart (no code change).
3. Wire the entry point on the entry agent's SOUL.md:
   - **cron**: add `schedule: "<cron expr>"` to its frontmatter
   - **Telegram**: add `channels: [telegram]` + the `send_telegram_message` tool
   - **HTTP**: `POST /dispatch {"to_agent": "...", "message": "..."}`
4. The `when:` strings in `dispatches_to:` are the routing conditions —
   plain prose, evaluated by the LLM that is already in the loop.
