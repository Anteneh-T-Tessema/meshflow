# MeshFlow Publishing Checklist

Complete step-by-step guide to publishing MeshFlow to every platform.
Run through this checklist for each new release.

---

## Pre-publish verification

```bash
# 1. Confirm version is consistent
python -c "import meshflow; print(meshflow.__version__)"   # should match
grep version pyproject.toml                                # pyproject.toml
# Both must match the tag you're about to create

# 2. Full test suite
.venv/bin/pytest --tb=short -q

# 3. Build the distribution
pip install build
python -m build
ls dist/   # should contain .tar.gz and .whl
```

---

## 1. PyPI — automated on git tag push

The `release.yml` GitHub Action publishes to PyPI automatically when you push a version tag.

```bash
# Tag and push — this triggers the release workflow
git tag v1.9.1
git push origin v1.9.1

# Monitor at:
# https://github.com/Anteneh-T-Tessema/meshflow/actions
```

**Verify after publish:**
```bash
pip install meshflow==1.9.1
python -c "from meshflow import Workflow, Agent, MeshFlowProxy; print('ok')"
```

---

## 2. Claude Code Skill — PR to anthropics/claude-code-skills

The skill file is at `docs/submissions/anthropic_skills_PR.md`.

```bash
# Fork and clone the Claude Code skills repo:
# https://github.com/anthropics/claude-code-skills  (check current URL)

# Copy the skill file into the fork
cp docs/submissions/anthropic_skills_PR.md skills/meshflow.md

# Push and open PR with title:
# "feat: MeshFlow skill — governed multi-agent orchestration"
```

**PR title:** `feat: add meshflow skill — production-safe multi-agent orchestration`

**PR description:** Use the content from `docs/submissions/anthropic_skills_PR.md` as the PR body.

---

## 3. Claude Desktop (MCP) — share config snippet

No submission required — users install directly. Share this in docs, README, and Discord:

```json
{
  "mcpServers": {
    "meshflow": {
      "command": "meshflow-mcp",
      "env": {
        "ANTHROPIC_API_KEY": "sk-ant-YOUR_KEY_HERE"
      }
    }
  }
}
```

**File location:**
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

**Full guide:** `docs/integrations/mcp_clients.md`

---

## 4. Smithery MCP Marketplace

Smithery is the primary MCP tool registry (smithery.ai).

```bash
# Option A — CLI (if smithery CLI is available)
npx @smithery/cli publish --config smithery.yaml

# Option B — GitHub integration
# 1. Go to https://smithery.ai
# 2. Sign in with GitHub
# 3. "Add Server" → point to https://github.com/Anteneh-T-Tessema/meshflow
# 4. Smithery auto-discovers smithery.yaml from the repo root
```

**Verify:** Search "meshflow" on https://smithery.ai after submission.

---

## 5. Cursor

No submission required. Users add to `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "meshflow": {
      "command": "meshflow-mcp",
      "env": { "ANTHROPIC_API_KEY": "sk-ant-..." }
    }
  }
}
```

Submit to Cursor marketplace (if they open one): https://cursor.com/plugins

---

## 6. OpenAI tool / GPT Actions

No app-store submission needed — teams integrate directly:

```python
from meshflow import meshflow_as_openai_tool
tool = meshflow_as_openai_tool()
# Pass to client.chat.completions.create(tools=[tool])
```

For **GPT Builder** (ChatGPT plugin system):
1. Deploy `meshflow serve` to a public URL (see Docker section below)
2. Export the OpenAPI spec: `meshflow serve --openapi > openapi.json`
3. In GPT Builder → "Actions" → import `openapi.json`

---

## 7. Anthropic Built with Claude

Application is at `docs/partnerships/anthropic_built_with_claude.md`.

Submit at: https://www.anthropic.com/built-with-claude

**Key facts to include:**
- v1.9.1, 4,749 tests, Apache 2.0
- First framework implementing Anthropic's Zero Trust for AI Agents spec
- HIPAA/SOX/GDPR/ISO 27001/EU AI Act compliance built in

---

## 8. Docker Hub — MCP server image

```bash
# Build
docker build -f Dockerfile.mcp -t meshflowdev/meshflow-mcp:1.9.1 .
docker tag meshflowdev/meshflow-mcp:1.9.1 meshflowdev/meshflow-mcp:latest

# Test locally
docker run -e ANTHROPIC_API_KEY=sk-ant-... meshflowdev/meshflow-mcp:1.9.1

# HTTP proxy mode
docker run -p 8080:8080 \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  meshflowdev/meshflow-mcp:1.9.1 \
  meshflow proxy --port 8080 --host 0.0.0.0

# Push
docker push antenehcodding/meshflow-mcp:1.9.2
docker push antenehcodding/meshflow-mcp:latest
```

---

## 9. npm (TypeScript SDK)

The TypeScript SDK is in `sdks/typescript/`.

```bash
cd sdks/typescript
npm version 1.9.1
npm publish --access public
```

**Verify:** `npm install meshflow-sdk@1.9.1`

---

## 10. Rust crate (crates.io)

```bash
cd sdks/rust
# Update version in Cargo.toml to 1.9.1
cargo publish
```

**Verify:** `cargo add meshflow-sdk@1.9.1`

---

## 11. Go module (pkg.go.dev)

Go modules are published via git tags. pkg.go.dev auto-discovers from GitHub.

```bash
cd sdks/go
# Ensure go.mod version matches
git tag sdks/go/v1.9.1
git push origin sdks/go/v1.9.1
```

**Verify:** https://pkg.go.dev/github.com/Anteneh-T-Tessema/meshflow/sdks/go

---

## 12. Java SDK (Maven Central)

```bash
cd sdks/java
mvn versions:set -DnewVersion=1.9.1
mvn deploy -P release
```

---

## 13. Product Hunt

Submit at: https://www.producthunt.com/posts/new

**All content ready at:** `docs/launch/product_hunt.md`

Best time: Tuesday–Thursday, 12:01 AM PT.

---

## 14. Show HN

Post at: https://news.ycombinator.com/submit

**Title:** Show HN: MeshFlow – Zero Trust + EU AI Act compliance for agents, built in by default (pip install meshflow)

**Content ready at:** `docs/launch/show_hn.md`

Best time: Tuesday–Thursday, 9–10 AM ET.

---

## 15. deepset / Haystack co-marketing

Send outreach email to Milos Rusic / Malte Pietsch at deepset.

**Email ready at:** `docs/partnerships/deepset_haystack.md` → "Email body (ready to send)"

---

## Post-publish verification checklist

After completing the above:

```bash
# PyPI
pip install meshflow==1.9.1 --dry-run

# MCP stdio works
echo '{"jsonrpc":"2.0","method":"tools/list","id":1}' | MESHFLOW_MOCK=1 meshflow-mcp

# Imports work
python -c "
from meshflow import (
    Workflow, Agent, MeshFlowProxy, MeshFlowHTTPProxy,
    meshflow_as_anthropic_tool, meshflow_as_openai_tool,
    PolicyToolCallInterceptor, governed_haystack_pipeline,
)
print('all imports ok')
"

# Test count
python -m pytest --co -q 2>/dev/null | tail -1
```

---

## Version bump procedure (for next release)

```bash
# 1. Update version in three places
sed -i 's/version = "1.9.1"/version = "1.9.2"/' pyproject.toml
# Edit meshflow/__init__.py: __version__ = "1.9.2"
# Edit smithery.yaml: version: 1.9.2
# Edit Dockerfile.mcp: LABEL version="1.9.2" + pip install "meshflow==1.9.2"

# 2. Add CHANGELOG.md entry

# 3. Commit + tag
git add -A && git commit -m "release: MeshFlow v1.9.2 — ..."
git tag v1.9.2
git push origin main --tags
# GitHub Actions handles PyPI + GitHub Release automatically
```
