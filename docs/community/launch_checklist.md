# MeshFlow Discord — Launch Checklist

---

## Pre-Launch (complete before opening the server to the public)

- [ ] **1. Create and configure the server** — set up all categories, channels, and permissions as defined in `discord_setup.md`. Lock #announcements and #changelog to admin/bot write access.
- [ ] **2. Write and pin the welcome message** — post the welcome message (from `discord_setup.md`) in #general and #welcome-and-rules. Pin it so it stays visible.
- [ ] **3. Configure reaction roles** — set up the Builder / Healthcare / Finance / Energy / Enterprise / Contributor self-selection roles using Carl-bot or MEE6.
- [ ] **4. Install and test bots** — install MEE6 (or Carl-bot), the GitHub integration, and the custom `!docs`/`!install` command bot. Test every command in #bot-playground before launch.
- [ ] **5. Seed #showcase with 3 example projects** — post 3 real showcase entries (one healthcare, one finance, one general) so the channel doesn't look empty on day one.
- [ ] **6. Seed #help with 5 answered questions** — pull common questions from GitHub Issues and pre-answer them in #help to demonstrate an active community.
- [ ] **7. Seed #roadmap-feedback with 3 open prompts** — post 3 specific questions (e.g., "Which compliance profile do you need next?") to seed discussion.
- [ ] **8. Invite beta users** — send personal invites to everyone who starred the GitHub repo, opened an issue, or emailed about MeshFlow. Target: 25–50 seed members before public launch.
- [ ] **9. Write the moderation runbook** — document how to handle spam, rule violations, and sensitive compliance questions. Share with all admins.
- [ ] **10. Set up GitHub → Discord webhook** — verify that new GitHub releases, issues, and PRs post automatically to #changelog and #announcements.

---

## Launch Day (execute in order)

1. **Post launch announcement in #announcements** — include the Product Hunt link, Show HN link, and a direct invite link. Pin it.
2. **Post in #general** — the welcome message goes live; tag @everyone once (the only time we use @everyone on launch day).
3. **Share the invite link everywhere at once** — Reddit (`r/MachineLearning`, `r/LocalLLaMA`, `r/Python`), Hacker News, LinkedIn, Twitter/X, and the Product Hunt discussion thread. Use the same invite link across all.
4. **Live Q&A session** — one maintainer on standby in #help and #general for the first 4 hours of launch to answer questions in real time.
5. **Monitor and moderate** — have a second team member watching for spam, duplicate questions, or compliance-sensitive content throughout the day.

---

## Week 1 Engagement Activities

1. **Daily "Tip of the Day" in #general (Mon–Fri)** — short, practical MeshFlow tips: e.g., how to enable HIPAA mode, how `stop_on_confidence` works, how to replay a failed workflow. Pre-write all five before launch.
2. **"What are you building?" thread in #introductions** — maintainer posts first, asking members to share their use case in one sentence. Reply to every single post in week 1.
3. **Bug bash incentive** — announce in #announcements: any verified bug filed and confirmed in week 1 earns a "Founding Contributor" Discord role and GitHub credit.
4. **Highlight one showcase post** — pick the best #showcase post from the week, write a 3–4 sentence summary of what the builder made, and re-post it in #announcements as "Community Spotlight #1."
5. **Retrospective and invite new members** — at the end of week 1, post a brief "Week 1 by the numbers" update in #announcements (member count, questions answered, bugs filed), and send a second wave of personal invites to GitHub watchers and newsletter subscribers.
