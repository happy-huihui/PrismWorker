---
name: project-conventions
description: >-
  Generate a project-conventions document through a warm, adaptive onboarding
  conversation. Trigger when the user wants to set up how an agent should work
  inside a project — e.g., "set up project conventions", "define how you should
  work here", "onboard yourself to this repo", "create project rules",
  "let's do onboarding", or when the project has no conventions document yet.
  Also trigger for updates: "update the conventions", "change how you work here",
  "tweak the working rules".
---

# Project Conventions

A conversational onboarding skill. Through 5–8 adaptive rounds, extract how the user wants an agent to work inside their project, then generate a tight `project-conventions.md` that the user can drop into the repository (or into the agent's long-term memory).

## Architecture

```
project-conventions/
├── SKILL.md                                    ← You are here. Core logic and flow.
├── templates/project-conventions.template.md   ← Output template. Read before generating.
└── references/conversation-guide.md            ← Detailed conversation strategies. Read at start.
```

**Before your first response**, read both:
1. `references/conversation-guide.md` — how to run each phase
2. `templates/project-conventions.template.md` — what you're building toward

## Ground Rules

- **One phase at a time.** 1–3 questions max per round. Never dump everything upfront.
- **Converse, don't interrogate.** React genuinely — surprise, humor, curiosity, gentle pushback. Mirror their energy and vocabulary.
- **Progressive warmth.** Each round should feel more informed than the last. By Phase 3, the user should feel understood.
- **Adapt pacing.** Terse user → probe with warmth. Verbose user → acknowledge, distill, advance.
- **Never expose the template.** The user is having a conversation, not filling out a form.
- **Never invent a convention.** Every line of the output must trace back to something the user actually said or clearly implied. An empty section beats a fabricated rule.

## Conversation Phases

The conversation has 4 phases. Each phase may span 1–3 rounds depending on how much the user shares. Skip or merge phases if the user volunteers information early.

| Phase | Goal | Key Extractions |
|-------|------|-----------------|
| **1. Hello** | Language + what this project is | Preferred language, project name and one-line purpose |
| **2. Context** | Where the project stands and what hurts | Tech stack, current stage, biggest time sinks, known landmines |
| **3. Working Style** | How the agent should actually work | Communication style, autonomy level, pushback preference, verification habit |
| **4. Boundaries** | Limits and direction | Dealbreakers, failure handling, long-term goal |

Phase details and conversation strategies are in `references/conversation-guide.md`.

## Extraction Tracker

Mentally track these fields as the conversation progresses. You need **all required fields** before generating.

| Field | Required | Source Phase |
|-------|----------|-------------|
| Preferred language | ✅ | 1 |
| Project name + one-line purpose | ✅ | 1 |
| Tech stack / key constraints | ✅ | 2 |
| Current stage | nice-to-have | 2 |
| Biggest time sinks | ✅ | 2 |
| Known landmines (past mistakes) | nice-to-have | 2 |
| Communication style | ✅ | 3 |
| Pushback / honesty preference | ✅ | 3 |
| Autonomy level | ✅ | 3 |
| Verification habit (how work gets proven) | ✅ | 3 |
| Dealbreakers / never-touch list | ✅ | 4 |
| Failure handling | ✅ | 4 |
| Long-term goal | nice-to-have | 4 |

If the user is direct and thorough, you can reach generation in 5 rounds. If they're exploratory, take up to 8. Never exceed 8 — if you're still missing fields, make your best inference, **mark it clearly as an inference**, and confirm.

## Generation

Once you have enough information:

1. Read `templates/project-conventions.template.md` if you haven't already.
2. Generate the document following the template structure exactly.
3. Save it with the sandbox `write_file` tool to `/mnt/user-data/outputs/project-conventions.md`.
   - The sandbox can only write under `/mnt/user-data`, so **do not attempt to write into the repository directly** — tell the user to move the file into the repo themselves.
   - Never claim you saved a file you did not actually save. If `write_file` returns an error, report it plainly instead of pretending it worked.
4. Present it warmly and ask for confirmation. Frame it as "here's how I understand we should work — does this feel right?"
5. Iterate until the user confirms.

**Generation rules:**
- The document is written in the language chosen in Phase 1. Do not force English.
- Every sentence must trace back to something the user said or clearly implied. No generic filler that would fit any project.
- Working rules are **behavioral rules**, not adjectives. Write "run the test suite before claiming a fix works" — not "be careful and thorough."
- If the user stated a fixed command (build / test / run), record it verbatim.
- Total length under 400 words. Density over length.
- The "Known landmines" section is a fixed empty placeholder — it gets filled over time, not during onboarding.
- If the user declines a non-required field, leave it out rather than inventing content.
