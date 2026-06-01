# MeshFlow Discord Community — Setup Guide

## Server Structure

### Categories and Channels

```
📣 INFORMATION
  #announcements        (read-only — admins/bots only)
  #changelog            (read-only — automated releases from GitHub)
  #roadmap-feedback     (post suggestions; team reviews weekly)

👋 GETTING STARTED
  #welcome-and-rules    (pinned: rules, onboarding links)
  #introductions        (tell us who you are and what you're building)
  #general              (open conversation — main hangout)

🛠️ DEVELOPMENT
  #showcase             (share what you've built with MeshFlow)
  #help                 (ask anything — tagged by maintainers)
  #bugs                 (paste error messages, stack traces)
  #integrations         (LangGraph, CrewAI, AutoGen, custom connectors)
  #token-optimization   (ModelRouter, ContextCompactor, caching tips)

🏛️ COMPLIANCE & GOVERNANCE
  #hipaa-sox-gdpr       (regulated-industry deployments)
  #audit-ledger         (tamper-evident chain, replay, forensics)
  #enterprise-policies  (DascGate, PII blocking, budget governance)

🏢 ENTERPRISE
  #enterprise-general   (private channel — request access for verified enterprise)
  #partnership-intros   (co-marketing, OEM, reseller conversations)

🤖 BOTS & TOOLS
  #bot-playground       (test bot commands without spamming other channels)
```

---

## Welcome Message (pin in #general)

> **Welcome to the MeshFlow community!**
>
> MeshFlow is the governance kernel for production multi-agent systems — HIPAA, SOX, GDPR, PCI, and NERC compliance baked in, tamper-evident audit chain included, zero vendor lock-in.
>
> **Start here:**
> - Install: `pip install meshflow`
> - Docs: https://meshflow.dev/docs
> - Quick start: [`QUICKSTART.md`](https://github.com/Anteneh-T-Tessema/meshflow/blob/main/QUICKSTART.md)
> - Examples: [`/examples`](https://github.com/Anteneh-T-Tessema/meshflow/tree/main/examples)
>
> Drop a line in #introductions, ask anything in #help, and share what you build in #showcase.
> We read every message. Let's ship something compliant. 🚀

---

## Onboarding Flow

### When Someone Joins

1. **Auto-welcome DM** (sent by bot within 5 seconds):

   > Hi {username} — welcome to MeshFlow!
   >
   > Here's your starter kit:
   > - `pip install meshflow` — you're one command away
   > - QUICKSTART.md: https://github.com/Anteneh-T-Tessema/meshflow/blob/main/QUICKSTART.md
   > - Full docs: https://meshflow.dev/docs
   > - If you're in a regulated industry (healthcare, finance, energy, legal), head to #compliance-governance — that's where the real conversation happens.
   >
   > Questions? Drop them in #help. We answer fast.

2. **Role Assignment** — members self-select via a reaction-role message pinned in #welcome-and-rules:

   | Reaction | Role | Unlocks |
   |----------|------|---------|
   | 🏗️ | Builder | #showcase, #help, #integrations |
   | 🏥 | Healthcare | #hipaa-sox-gdpr |
   | 🏦 | Finance | #hipaa-sox-gdpr |
   | ⚡ | Energy/Utilities | #hipaa-sox-gdpr |
   | 🏢 | Enterprise | (team manually upgrades to enterprise-general) |
   | 🤝 | Contributor | #dev-meta (maintainers + contributors) |

3. **Pinned resources in #help**:
   - QUICKSTART.md link
   - Docs site link
   - GitHub Issues link
   - Stack Overflow tag: `meshflow`

---

## Community Rules

1. **Be specific when asking for help.** Include your MeshFlow version, relevant code snippet, and full error message. Vague questions get slow answers.
2. **No promotional spam.** Sharing tools, projects, or services is welcome in #showcase — unsolicited DMs and off-topic ads will result in a ban.
3. **Compliance conversations stay constructive.** Do not share real PII, PHI, or confidential production data in any channel — ever.
4. **Respect everyone.** This community spans students, solo founders, and Fortune 500 engineers. Condescension, harassment, or gatekeeping will not be tolerated.
5. **Report bugs in #bugs, not #general.** Include a minimal reproducible example. Duplicate issues will be closed with a link to the original.

---

## Bot Commands

Set up the following slash commands using a Discord bot (e.g., MEE6, custom Discord.py bot, or Pylon):

| Command | Response |
|---------|----------|
| `!docs` | Returns: "MeshFlow docs: https://meshflow.dev/docs" |
| `!install` | Returns: "`pip install meshflow` — requires Python 3.10+" |
| `!quickstart` | Returns: "QUICKSTART.md: https://github.com/Anteneh-T-Tessema/meshflow/blob/main/QUICKSTART.md" |
| `!compliance` | Returns: "Compliance profiles (HIPAA/SOX/GDPR/PCI/NERC): https://meshflow.dev/docs/compliance" |
| `!changelog` | Returns: "Latest release notes: https://github.com/Anteneh-T-Tessema/meshflow/blob/main/CHANGELOG.md" |
| `!examples` | Returns: "Example agents and workflows: https://github.com/Anteneh-T-Tessema/meshflow/tree/main/examples" |
| `!roadmap` | Returns: "Public roadmap: https://github.com/Anteneh-T-Tessema/meshflow/blob/main/ROADMAP.md" |
| `!bug` | Returns: "File a bug: https://github.com/Anteneh-T-Tessema/meshflow/issues/new?template=bug_report.md" |

### Recommended Bots to Install
- **MEE6** — moderation, welcome messages, reaction roles, leveling
- **Carl-bot** — automod, reaction roles, logging
- **GitHub bot** (official) — posts PR/issue/release notifications to #changelog and #announcements
- **Custom MeshFlow bot** (optional, build with `discord.py`) — for `!docs`, `!install`, etc. commands

---

## Moderation Notes

- **Slow mode** on #help: 10-second cooldown to reduce noise.
- **Auto-archive** threads in #help after 7 days of inactivity.
- Maintainers should check #bugs and #help at minimum twice per week.
- Monthly "Office Hours" voice session — announced in #announcements, held in a temporary voice channel.
