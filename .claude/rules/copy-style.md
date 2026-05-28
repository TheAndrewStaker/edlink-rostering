---
paths:
  - "**/*.md"
  - "**/*.tsx"
  - "**/*.ts"
  - "**/*.py"
  - "**/CHANGELOG*"
  - "**/README*"
---

# Copy style

Universal rule for human-facing prose: in docs, UI copy, error messages, code comments, commit messages, PR descriptions, and customer communication. Applies to anything a person will read.

## No em-dashes

Never use em-dashes (—) or en-dashes (–) in prose. Use a comma, a period, parentheses, or a colon instead.

```
Bad:  "The application integrates with district SIS systems — OneRoster, Ed-Fi, or direct — through certified partners."
Good: "The application integrates with district SIS systems (OneRoster, Ed-Fi, or direct) through certified partners."
Good: "The application integrates with district SIS systems through certified partners. Supported standards: OneRoster, Ed-Fi, and direct."
```

Em-dashes are an AI-tell. Avoid them across the board.

This is non-negotiable. Search-and-replace `—` and `–` before any prose ships.

## No "at the intersection of"

Don't use the phrase "at the intersection of." It's an AI-tell.

```
Bad:  "The application operates at the intersection of special education, data interoperability, and AI."
Good: "The application connects special education data systems and applies AI to IEP workflows."
```

## No AI-tell phrases

A non-exhaustive list. Avoid:

- "in today's fast-paced world"
- "navigating the complexities of"
- "leveraging cutting-edge"
- "harnessing the power of"
- "unlocking the potential of"
- "in an era where"
- "the landscape of [X] is rapidly evolving"
- "delve into" / "delve deeper"
- "tapestry of"
- "myriad of"
- "plethora of"
- "the journey of [X]"
- "embark on"
- "robust and scalable solution"
- "seamless integration"
- "transform the way we"

If a phrase pattern shows up in marketing-AI generated text, it shows up in this list. Strip it.

## No filler intensifiers

```
Bad:  "The application is truly committed to deeply caring about student outcomes."
Good: "The application cares about student outcomes."
```

"Truly," "deeply," "really," "very," "incredibly" — almost always cut.

## Plain English

```
Bad:  "Utilize the connector framework to facilitate data ingest."
Good: "Use the connector framework to ingest data."

Bad:  "In order to commence the integration..."
Good: "To start the integration..."

Bad:  "Subsequent to authentication..."
Good: "After authentication..."
```

Shorter, plainer Anglo-Saxon-rooted words beat their Latinate cousins almost every time.

## Active voice

```
Bad:  "The IEP is reviewed annually by the team."
Good: "The team reviews the IEP annually."
```

Passive voice is appropriate when the actor doesn't matter or is unknown ("data is encrypted at rest"). Otherwise, active.

## Concrete over abstract

```
Bad:  "The application provides solutions for educational outcomes."
Good: "The application gives special education teachers a single view of every student's IEP progress."
```

Abstract claims are easy to make and hard to verify. Concrete claims commit you to something.

## Commit messages and PR descriptions

Same rules. Write commit messages a teammate could read in five months and understand:

```
Bad:  "Updates"
Bad:  "fixes the thing"
Bad:  "Implementing the seamless integration of multi-tenant authentication"
Good: "Add lea_id check to StudentRepository.list"
Good: "Fix off-by-one in school-year boundary date parsing"
```

PR description format:

```
## Summary
One paragraph explaining what changed and why.

## Verification
- [ ] Unit tests added/updated
- [ ] Manual verification: <what you tested>

## Risk
What could go wrong if this is bad. What rollback looks like.
```

## Error messages

User-facing error messages are concise, actionable, and don't contain stack traces or internal details:

```
Bad:  "An unexpected error has occurred while attempting to facilitate the seamless retrieval of the requested educational record. Please try again later."
Bad:  "NullPointerException at line 142 in StudentService"
Good: "We couldn't find that student. Try refreshing or check the student ID."
```

Internal error logs are different — log everything you need to debug. The customer-facing message is the trimmed version.

## Documentation

Docs follow the rules above plus:

- Lead with the conclusion, not the setup
- Examples come early, not at the end
- Code blocks have language tags for syntax highlighting
- Cross-references are explicit (link to the file, not "see other docs")
- Verification status is marked (`[VERIFY]` for pending confirmation)

## Tone

Warm, direct, technically grounded. Not condescending. Not breathless. Not aspirational without backing claims.

```
Bad:  "The application is excited to revolutionize special education with cutting-edge AI."
Good: "The application helps special education teachers spend less time on documentation and more time with students."
```

## Customer-facing copy

Marketing site, app UI, customer emails, support docs — all follow the same rules. Plus:

- Don't make claims that legal hasn't reviewed
- Don't overstate compliance posture (FERPA-compliant ≠ HIPAA-compliant; ESSA Tier 4 ≠ Tier 1)
- Don't reference customers by name without permission
- Don't claim certifications you don't have

## Linting prose

Where feasible, lint:

- `markdownlint` for Markdown structure
- `prettier` for code-adjacent files
- Custom regex pre-commit hooks for em-dashes and the AI-tell list

```
# pre-commit hook
if grep -P '[—–]' file.md; then
    echo "Em-dash detected. Use a comma, period, or parentheses."
    exit 1
fi
```

## Cross-references

