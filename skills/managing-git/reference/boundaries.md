# Who decides what

| Script decides (never override) | You decide | User decides (always ask) |
|---|---|---|
| message format, allowed types/scopes | how to group files into commits | anything in "Destructive ops" |
| secret block, preflight safety checks | commit subject/body wording | splitting into >1 commit |
| when to ask (protected branch = WARN) | | committing directly on a protected branch |
| allowed types, subject rules | which type (feat, fix, refactor, …) | merge vs rebase strategy |
| conflict prediction, branch naming | how to resolve a conflict hunk | false-positive secret override |
