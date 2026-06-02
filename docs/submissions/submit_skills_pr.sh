#!/usr/bin/env bash
# submit_skills_pr.sh — fork and open PR to anthropics/claude-code-skills
#
# Prerequisites:
#   gh auth login   (GitHub CLI, one-time setup)
#   gh --version >= 2.40
#
# Usage:
#   chmod +x docs/submissions/submit_skills_pr.sh
#   ./docs/submissions/submit_skills_pr.sh

set -e

SKILLS_REPO="anthropics/claude-code-skills"
SKILL_FILE="docs/submissions/anthropic_skills_PR.md"
BRANCH="feat/meshflow-skill-v1.9"
FORK_DIR="/tmp/claude-code-skills-fork"

echo ""
echo "  MeshFlow → Claude Code Skills PR Submission"
echo "  ============================================"
echo ""

# Check gh is available
if ! command -v gh &>/dev/null; then
  echo "  Error: gh CLI not found."
  echo "  Install: https://cli.github.com/"
  echo ""
  echo "  Alternative: Open a PR manually at:"
  echo "  https://github.com/${SKILLS_REPO}/compare"
  echo "  and paste the content of ${SKILL_FILE}"
  exit 1
fi

# Check auth
if ! gh auth status &>/dev/null; then
  echo "  Not authenticated. Run: gh auth login"
  exit 1
fi

echo "  1. Forking ${SKILLS_REPO}..."
gh repo fork "${SKILLS_REPO}" --clone=false 2>/dev/null || true

ACTOR=$(gh api user --jq '.login')
FORK="${ACTOR}/claude-code-skills"

echo "  2. Cloning fork..."
rm -rf "${FORK_DIR}"
gh repo clone "${FORK}" "${FORK_DIR}" -- --depth=1

echo "  3. Creating branch ${BRANCH}..."
cd "${FORK_DIR}"
git checkout -b "${BRANCH}"

echo "  4. Copying skill file..."
mkdir -p skills
SKILL_NAME="meshflow.md"
cp "${OLDPWD}/${SKILL_FILE}" "skills/${SKILL_NAME}"

echo "  5. Committing..."
git add "skills/${SKILL_NAME}"
git commit -m "feat: add meshflow skill — production-safe multi-agent orchestration v1.9"

echo "  6. Pushing..."
git push -u origin "${BRANCH}"

echo "  7. Opening PR..."
PR_URL=$(gh pr create \
  --repo "${SKILLS_REPO}" \
  --title "feat: add meshflow skill — production-safe multi-agent orchestration" \
  --body "$(cat "${OLDPWD}/${SKILL_FILE}")" \
  --head "${ACTOR}:${BRANCH}" \
  --base main)

echo ""
echo "  PR opened: ${PR_URL}"
echo ""
echo "  Done!"
