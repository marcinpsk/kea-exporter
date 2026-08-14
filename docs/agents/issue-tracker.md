# Issue tracker: GitHub

Issues and specs for this repo live as GitHub issues. Use the `gh` CLI for all operations.

## Conventions

Set `issue_number` to the issue number before commands that use it.

- **Create an issue**: `gh issue create --title "..." --body "..."`. Use a heredoc for multi-line bodies.
- **Read an issue**: `gh issue view "$issue_number" --json title,body,comments,labels --jq '{title, body, comments: [.comments[].body], labels: [.labels[].name]}'`. Use `--comments` instead when you want the human-readable rendering rather than JSON.
- **List issues**: `gh issue list --state open --json number,title,body,labels --jq '[.[] | {number, title, body, labels: [.labels[].name]}]'` with appropriate `--label` and `--state` filters. For comment bodies, read each issue with `gh issue view "$issue_number" --json comments`: `gh issue list` returns at most 100 comments per issue and gives no sign that it truncated.
- **Comment on an issue**: `gh issue comment "$issue_number" --body "..."`
- **Apply / remove labels**: `gh issue edit "$issue_number" --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close "$issue_number" --comment "..."`

Infer the repo from `git remote -v` — `gh` does this automatically when run inside a clone.

## Pull requests as a triage surface

**PRs as a request surface: no.** _(Set to `yes` if this repo treats external PRs as feature requests; `/triage` reads this flag.)_

When set to `yes`, PRs run through the same labels and states as issues, using the `gh pr` equivalents. Set `pr_number` before commands that use it:

- **Read a PR**: `gh pr view "$pr_number" --comments` and `gh pr diff "$pr_number"` for the diff.
- **List external PRs for triage**: `gh pr list --json` has no author-association field, so read it from REST: `gh api "repos/{owner}/{repo}/pulls?state=open&per_page=100" --paginate --jq '.[] | select(.author_association | IN("OWNER","MEMBER","COLLABORATOR") | not) | {number, title, author: .user.login, association: .author_association}'`. Drop the three insider associations rather than listing the outsider ones: `author_association` also has `FIRST_TIMER` and `MANNEQUIN`, and GitHub may add more, so a positive list silently loses contributors.
- **Comment / label / close**: `gh pr comment`, `gh pr edit --add-label`/`--remove-label`, `gh pr close`.

GitHub shares one number space across issues and PRs, so a bare `#42` may be either. Resolve with `gh pr view "$issue_number"` and fall back to `gh issue view "$issue_number"`.

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Run `gh issue view "$issue_number" --comments`.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a single issue with **child** issues as tickets. Run only one `/wayfinder` session per map. GitHub assignment is not an atomic claim.

- **Map**: a single issue labelled `wayfinder:map`, holding the Notes / Decisions-so-far / Fog body. `gh issue create --label wayfinder:map`.
- **Child ticket**: an issue linked to the map as a GitHub sub-issue (`gh api` on the sub-issues endpoint). Where sub-issues aren't enabled, add the child to a task list in the map body and put `Part of #<map>` at the top of the child body. Labels: `wayfinder:<type>` (`research`/`prototype`/`grilling`/`task`). Once claimed, the ticket is assigned to the driving dev.
- **Blocking**: GitHub's **native issue dependencies** — the canonical, UI-visible representation. Set `child_number` and `blocker_database_id`, then add an edge with `gh api --method POST "repos/{owner}/{repo}/issues/${child_number}/dependencies/blocked_by" -F "issue_id=${blocker_database_id}"`. Get the blocker's numeric **database id** with `gh api "repos/{owner}/{repo}/issues/${issue_number}" --jq .id`, not the `#number` or `node_id`. GitHub reports `issue_dependencies_summary.blocked_by` (open blockers only — the live gate). Where dependencies aren't available, fall back to a `Blocked by: #<n>, #<n>` line at the top of the child body. A ticket is unblocked when every blocker is closed.
- **Frontier query**: set `map_number`, then enumerate the map's children in map order first, from the sub-issues endpoint (`gh api --paginate "repos/{owner}/{repo}/issues/${map_number}/sub_issues?per_page=100" --jq '.[].number'`, which pages at 30 without `--paginate`) or from the task list in the map body. Set `child_number` for each result, then inspect it with `gh issue view "$child_number" --json number,title,state,assignees,blockedBy --jq 'select(.state == "OPEN" and (.assignees | length == 0) and ([.blockedBy.nodes[] | select(.state == "OPEN")] | length == 0))'`. Take the first child for which the command returns a result. Do not reach for `gh issue list` here: it queries every issue in the repository, returns 30 by default, and does not preserve map order. Reading the same fact over REST instead, the field is `.issue_dependencies_summary.blocked_by` (open blockers only). Where dependencies aren't available, fall back to an open issue named in the `Blocked by` line.
- **Claim**: `gh issue edit "$issue_number" --add-assignee @me` — the session's first write.
- **Resolve**: set `answer`, run `gh issue comment "$issue_number" --body "$answer"`, then run `gh issue close "$issue_number"`, then append a context pointer (gist + link) to the map's Decisions-so-far.
