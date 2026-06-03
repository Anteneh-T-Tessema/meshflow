# MeshFlow Discord Community — Setup Guide (v1.10.0)

## Server Structure

### Categories and Channels

```
📣 INFORMATION
  #announcements        (read-only — admins/bots only)
  #changelog            (read-only — automated releases from GitHub Actions)
  #roadmap-feedback     (post suggestions; reviewed weekly)

👋 GETTING STARTED
  #welcome-and-rules    (pinned: rules, onboarding links, reaction roles)
  #introductions        (who you are + what you're building)
  #general              (main hangout)

🛠️ DEVELOPMENT
  #showcase             (share what you've shipped with MeshFlow)
  #help                 (ask anything — tagged by maintainers)
  #bugs                 (paste error + minimal repro; maintainers triage)
  #integrations         (LangGraph, CrewAI, AutoGen, custom connectors)

🤖 ROUTING & COST
  #model-routing        (ModelTierRouter, AdaptiveModelTierRouter, CascadeRouter)
  #cost-optimization    (estimate_cost, cost-report CLI, local vs cloud)
  #thompson-sampling    (TS posteriors, router.history(), per-bucket learning)

📊 OBSERVABILITY
  #streaming            (Workflow.stream, astream, SSE helpers, FastAPI patterns)
  #structured-output    (run_structured, astream_structured, Pydantic models)
  #trace-studio         (meshflow studio, replay, audit ledger, ZT posture)

🏛️ COMPLIANCE & GOVERNANCE
  #hipaa-sox-gdpr       (regulated-industry deployments)
  #audit-ledger         (tamper-evident chain, replay, forensics)
  #enterprise-policies  (DascGate, PII blocking, budget governance)

🏢 ENTERPRISE
  #enterprise-general   (private — request access for verified enterprise orgs)
  #partnership-intros   (co-marketing, OEM, reseller)

🤖 BOTS & TOOLS
  #bot-playground       (test commands without spamming other channels)
```

---

## #announcements — Launch Post

> **MeshFlow is live on Discord.**
>
> MeshFlow is the governance kernel for production multi-agent pipelines.
> v1.10.0 — just shipped — adds something genuinely new: a routing system
> that gets cheaper and smarter every time it runs.
>
> **The idea:** start with the cheapest model. Escalate only when the agent
> isn't confident enough. Learn which tier handles which task type well.
> Never touch the routing code again.
>
> ```python
> from meshflow import AdaptiveModelTierRouter, ModelTier, CascadeRouter
>
> router = AdaptiveModelTierRouter(
>     tiers=[
>         ModelTier("fast",  "llama3.2",   max_tokens=512),   # $0.00 local
>         ModelTier("smart", "mistral:7b", max_tokens=2048),  # $0.00 local
>         ModelTier("large", "gpt-4o",     max_tokens=4096),  # pay only here
>     ],
>     adapt_every=50,   # auto-adjust thresholds every 50 routes
> )
>
> cascade = CascadeRouter(router, escalation_threshold=0.65)
> # → llama3.2 first. CONFIDENCE:0.90? Done. $0.
> # → CONFIDENCE:0.40? Retry with mistral. $0.
> # → Still low? gpt-4o. Pay once.
> ```
>
> Everything else: SHA-256 audit chain, HIPAA/SOX/GDPR/PCI/NERC profiles,
> hard cost caps, SSE streaming, Go SDK, 5182 tests.
>
> Install: `pip install meshflow`
> Docs: https://meshflow.dev/docs
> GitHub: https://github.com/Anteneh-T-Tessema/meshflow
>
> Drop a line in #introductions. Ask anything in #help.
> We read every message.

---

## Welcome Message (pin in #general)

> **Welcome to the MeshFlow community!**
>
> MeshFlow is a governed multi-agent framework — compliance, cost caps,
> and a tamper-evident audit chain built in from line one.
>
> **v1.10.0 highlights:**
> - Self-improving cascade router (Thompson Sampling + per-bucket posteriors)
> - `CascadeRouter` — start cheap, escalate only on low confidence
> - `AdaptiveModelTierRouter` — learns which model tier fits which task type
> - `router.history()` — see the last N routing decisions + TS state
> - `Workflow.run_structured()` / `astream_structured()` — Pydantic output
> - Redis + File memory backends for cross-session agent memory
> - Go SDK: multimodal, batch, streaming, structured output
>
> **Start here:**
> - Install: `pip install meshflow`
> - Docs: https://meshflow.dev/docs
> - Quick start: https://github.com/Anteneh-T-Tessema/meshflow/blob/main/QUICKSTART.md
> - Examples: https://github.com/Anteneh-T-Tessema/meshflow/tree/main/examples
> - Routing example: `examples/adaptive_routing.py`
> - Cascade example: `examples/cascade_routing.py`
>
> Post in #introductions → ask in #help → share in #showcase.
> We read every message. 🚀

---

## Onboarding Flow

### When Someone Joins

1. **Auto-welcome DM** (sent by bot within 5 seconds):

   > Hi {username} — welcome to MeshFlow!
   >
   > **One-liner to get started:**
   > ```
   > pip install meshflow
   > MESHFLOW_MOCK=1 python examples/adaptive_routing.py
   > ```
   >
   > **Key links:**
   > - Docs: https://meshflow.dev/docs
   > - QUICKSTART: https://github.com/Anteneh-T-Tessema/meshflow/blob/main/QUICKSTART.md
   > - What's new in v1.10.0: CHANGELOG.md
   >
   > If you're in healthcare, finance, energy, or legal → #hipaa-sox-gdpr
   > If you're building routing pipelines → #model-routing
   > Questions? → #help (we respond fast)

2. **Reaction roles** (pin in #welcome-and-rules):

   | Reaction | Role | Unlocks |
   |---|---|---|
   | 🏗️ | Builder | #showcase, #help, #integrations |
   | 🤖 | Routing | #model-routing, #cost-optimization, #thompson-sampling |
   | 📊 | Observability | #streaming, #structured-output, #trace-studio |
   | 🏥 | Healthcare | #hipaa-sox-gdpr |
   | 🏦 | Finance | #hipaa-sox-gdpr |
   | ⚡ | Energy | #hipaa-sox-gdpr |
   | 🏢 | Enterprise | (manually upgraded to #enterprise-general) |
   | 🤝 | Contributor | #dev-meta |

3. **Pinned in #help:**
   - QUICKSTART.md
   - https://meshflow.dev/docs
   - https://github.com/Anteneh-T-Tessema/meshflow/issues
   - `!bug` command for filing issues
   - Stack Overflow tag: `meshflow`

---

## Community Rules

1. **Be specific when asking for help.** Paste your MeshFlow version
   (`import meshflow; print(meshflow.__version__)`), a minimal repro, and
   the full error traceback. Vague questions get slow answers.
2. **No promotional spam.** Tools and projects welcome in #showcase —
   unsolicited DMs and off-topic ads result in a ban.
3. **Compliance conversations stay constructive.** Never paste real PII,
   PHI, or production data. Ever.
4. **Respect everyone.** This community spans students, solo founders, and
   Fortune 500 engineers. No gatekeeping, no condescension.
5. **Routing questions go in #model-routing, not #general.** Include
   `router.explain(task)` output and `router.history(5)` when asking.
6. **Bugs go in #bugs with a minimal repro.** Duplicates get closed with a
   link to the original.

---

## Bot Commands

| Command | Response |
|---|---|
| `!docs` | MeshFlow docs: https://meshflow.dev/docs |
| `!install` | `pip install meshflow` — requires Python 3.11+ |
| `!quickstart` | https://github.com/Anteneh-T-Tessema/meshflow/blob/main/QUICKSTART.md |
| `!version` | Latest: v1.10.0 — https://pypi.org/project/meshflow/ |
| `!changelog` | https://github.com/Anteneh-T-Tessema/meshflow/blob/main/CHANGELOG.md |
| `!examples` | https://github.com/Anteneh-T-Tessema/meshflow/tree/main/examples |
| `!roadmap` | https://github.com/Anteneh-T-Tessema/meshflow/blob/main/ROADMAP.md |
| `!compliance` | HIPAA/SOX/GDPR/PCI/NERC: https://meshflow.dev/docs/compliance |
| `!router` | Routing docs: examples/adaptive_routing.py + examples/cascade_routing.py |
| `!cascade` | CascadeRouter: start cheap, escalate on low confidence — see examples/cascade_routing.py |
| `!cost` | `meshflow cost-report --help` and `wf.estimate_cost(task)` |
| `!memory` | Redis/SQLite/File/Postgres backends — https://meshflow.dev/docs/memory |
| `!go` | Go SDK: `go get github.com/Anteneh-T-Tessema/meshflow/sdks/go` |
| `!bug` | https://github.com/Anteneh-T-Tessema/meshflow/issues/new?template=bug_report.md |

### Recommended Bots
- **MEE6** — moderation, welcome DMs, reaction roles, leveling
- **Carl-bot** — automod, reaction roles, logging
- **GitHub bot** (official) — posts releases to #changelog, PRs to #announcements
- **MeshFlow bot** — custom discord.py bot (script below)

---

## discord.py Bot Script

Save as `bot.py`, install `pip install discord.py`, set `DISCORD_TOKEN` env var:

```python
"""MeshFlow Discord bot — handles !commands and auto-welcome DMs."""
import os
import discord
from discord.ext import commands

TOKEN = os.environ["DISCORD_TOKEN"]

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

COMMANDS = {
    "docs":        "MeshFlow docs: https://meshflow.dev/docs",
    "install":     "`pip install meshflow` — requires Python 3.11+",
    "quickstart":  "https://github.com/Anteneh-T-Tessema/meshflow/blob/main/QUICKSTART.md",
    "version":     "Latest: **v1.10.0** — https://pypi.org/project/meshflow/",
    "changelog":   "https://github.com/Anteneh-T-Tessema/meshflow/blob/main/CHANGELOG.md",
    "examples":    "https://github.com/Anteneh-T-Tessema/meshflow/tree/main/examples",
    "roadmap":     "https://github.com/Anteneh-T-Tessema/meshflow/blob/main/ROADMAP.md",
    "compliance":  "HIPAA/SOX/GDPR/PCI/NERC: https://meshflow.dev/docs/compliance",
    "router":      "Routing: `examples/adaptive_routing.py` + `examples/cascade_routing.py`",
    "cascade":     "CascadeRouter — start cheap, escalate on low confidence:\n```python\ncascade = CascadeRouter(router, escalation_threshold=0.65)\n```",
    "cost":        "`meshflow cost-report --help`  |  `wf.estimate_cost(task)`",
    "memory":      "Redis/SQLite/File/Postgres memory backends: https://meshflow.dev/docs/memory",
    "go":          "Go SDK: `go get github.com/Anteneh-T-Tessema/meshflow/sdks/go`",
    "bug":         "File a bug: https://github.com/Anteneh-T-Tessema/meshflow/issues/new?template=bug_report.md",
}

WELCOME_DM = """\
Hi {name} — welcome to MeshFlow!

**One-liner to get started:**
```
pip install meshflow
MESHFLOW_MOCK=1 python examples/adaptive_routing.py
```

**Key links:**
• Docs: https://meshflow.dev/docs
• QUICKSTART: https://github.com/Anteneh-T-Tessema/meshflow/blob/main/QUICKSTART.md
• What's new in v1.10.0: https://github.com/Anteneh-T-Tessema/meshflow/blob/main/CHANGELOG.md

If you're building routing pipelines → #model-routing
If you're in a regulated industry → #hipaa-sox-gdpr
Questions? → #help (we respond fast)
"""


@bot.event
async def on_ready():
    print(f"MeshFlow bot ready: {bot.user}")


@bot.event
async def on_member_join(member: discord.Member):
    try:
        await member.send(WELCOME_DM.format(name=member.display_name))
    except discord.Forbidden:
        pass   # DMs disabled — skip silently


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    content = message.content.strip()
    if content.startswith("!"):
        cmd = content[1:].split()[0].lower()
        if cmd in COMMANDS:
            await message.channel.send(COMMANDS[cmd])
            return
    await bot.process_commands(message)


@bot.command(name="help")
async def help_cmd(ctx):
    cmds = "\n".join(f"`!{k}`" for k in sorted(COMMANDS))
    await ctx.send(f"**MeshFlow bot commands:**\n{cmds}")


if __name__ == "__main__":
    bot.run(TOKEN)
```

Run: `DISCORD_TOKEN=your_token python bot.py`

---

## GitHub Actions → Discord Integration

Add to `.github/workflows/release.yml` to post release notes to #changelog:

```yaml
- name: Notify Discord
  if: success()
  env:
    DISCORD_WEBHOOK: ${{ secrets.DISCORD_CHANGELOG_WEBHOOK }}
  run: |
    VERSION="${{ github.ref_name }}"
    curl -X POST "$DISCORD_WEBHOOK" \
      -H "Content-Type: application/json" \
      -d "{\"content\": \"**MeshFlow ${VERSION} released** — \`pip install meshflow==${VERSION#v}\`\n\nChangelog: https://github.com/Anteneh-T-Tessema/meshflow/blob/main/CHANGELOG.md\nPyPI: https://pypi.org/project/meshflow/${VERSION#v}/\"}"
```

Secrets to add: `DISCORD_CHANGELOG_WEBHOOK`, `DISCORD_ANNOUNCEMENTS_WEBHOOK`

---

## Moderation Notes

- **#help slow mode:** 10-second cooldown.
- **#bugs slow mode:** 30-second cooldown (forces people to read existing bugs).
- **#model-routing:** Pin `router.explain()` output format as a sticky.
- **Auto-archive #help threads** after 7 days of inactivity.
- Maintainers check #bugs and #help minimum **twice per week**.
- Monthly **Office Hours** voice session — announced in #announcements.
- Quarterly **showcase thread** — top 5 community projects get retweeted.

---

## Launch Checklist

- [ ] Create Discord server
- [ ] Set up categories and channels (see structure above)
- [ ] Pin welcome message in #general
- [ ] Set up reaction roles in #welcome-and-rules
- [ ] Install MEE6 + Carl-bot
- [ ] Install official GitHub bot → connect to repo → route to #changelog
- [ ] Add Discord webhook secrets to GitHub repo
- [ ] Deploy `bot.py` (Railway, Fly.io, or any always-on host)
- [ ] Post #announcements launch message (text above)
- [ ] Post invite link on GitHub README, PyPI page, Show HN, Product Hunt
- [ ] Cross-post in r/Python, r/MachineLearning with community link
