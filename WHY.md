# Why this isn't another LangGraph chatbot

The brief asked for an *"AI Agent Orchestration Platform"* and most submissions
will read that as **"build a Telegram chatbot that orchestrates LangGraph
agents on demand."** That's the obvious shape. It's also wrong for Yuno.

Yuno is payments orchestration. Payments infra is not request-response. Fraud
scanners run on a schedule. Retry queues drain themselves. Settlement
reconciliations notice anomalies and surface them. **The system does work
*before* you ask.** A chatbot on top is just where the human reads the result.

This build inverts the default:

| Most submissions | This build |
|---|---|
| Chat-first: human asks, agents respond | **Always-on**: agents wake on cron / events, *then* notify a human |
| LangGraph workflow per request | **Actor model**: one inbound queue + one worker coroutine per agent |
| Agents in DB rows | **Agents are files** (`agents/*.md`). Git is the version control |
| Edit history in some audit table | **`/diff` view**: `git diff` between two commits of `agents/` |
| Conditions in a DSL or rule engine | **Prose `when:` conditions** in each `dispatches_to:` entry; the LLM is the router |
| MCP servers for tools | **Plain Python `@tool` functions**; one decorator, no extra process |
| Generic "research assistant" demo | **Fraud-monitoring demo**: fraud_screener → concierge → investigator → resolver |

## What this buys

**For the demo.** The fraud_monitor scenario maps directly to Yuno's actual
product surface. A reviewer watching the gif sees something that looks like
Yuno's own internal dashboard, not a generic AI tool.

**For the code review.** The actor model is 135 LOC. The custom runtime is
175. The `/diff` endpoint is 25 (it just shells out to `git diff`). Every
choice traces to a sentence: *agents are files, so git, so `/diff` is free*.

**For the next 90 days of hypothetical product work.** Adding a new agent =
adding a Markdown file. Adding a new channel = implementing a 3-method
interface and registering one `await start()` in the lifespan. Adding a new
tool = adding a `@tool`-decorated function in `tools.py`. Each of these is
purely additive — no schema migrations, no graph rewrites.

## What it gives up

- No checkpointing of in-flight runs. The actor model bounds the loss to one
  inbound dispatch per agent if the process dies mid-turn. SQLite has the
  audit log of completed runs.
- No visual graph editor like LangGraph Studio. The canvas in this build is
  read-only-for-routing (edit nodes via the modal, but not drag-to-connect).
  Routing changes are SOUL.md edits, which `/diff` then audits.
- No multi-LLM provider. One model family (Anthropic, Sonnet + Haiku). The
  cost of adding another (Groq, OpenAI) is one PR.

## What I'd build next

1. **Shadow-mode deployment.** Run a new agent version in parallel with the
   live one; compare outputs side by side; promote when confident. This is
   how payment processors A/B test new pricing rules. Falls naturally out of
   agents-as-files + git.
2. **Webhook-triggered agents.** Today agents wake on cron or HTTP-injected
   dispatch. Adding a webhook source (Stripe events, Yuno's own callback)
   is the same `enqueue()` call with a different upstream.
3. **Per-tenant routing.** A second SOUL.md frontmatter field (`tenants:`)
   plus a tenant tag on every dispatch. Same actor model, more queues.

## The 30-second pitch

> The differentiator is not the framework. It's the *shape*: always-on,
> file-based agents that talk via an async queue. Most candidates will ship
> a chatbot wrapper on LangGraph. I shipped a small system that **looks and
> feels like payments infrastructure**, because that's the product Yuno
> actually sells.

— end —
