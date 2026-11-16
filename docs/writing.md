# Writing guidelines

How k8srca's documents are written: the README, the designs, the plans and everything under
`docs/`. They exist so that someone new to the project, or a coding agent, can act on them
without asking. Review checks documents against this page
([development.md, "Review"](development.md#review)).

## Who you are writing for

A senior engineer who started on the project today. They know Kubernetes, Python and how
language-model agents work in general. They do not know this project's history, its private
vocabulary or what was decided last week.

## Accuracy

- **Describe the system as it is.** Check a claim against the code before writing it. When the
  code and a document disagree, fix one of them in the same change
  ([constitution A8](constitution.md#a8-the-documents-describe-the-system-as-it-is)).
- **Label anything that is not built.** Planned, on-hold or abandoned work is marked where it
  appears, never described in the present tense as if it runs:
  - a whole section starts with a blockquote: `> **Not built.** ...`, `> **Planned.** ...` or
    `> **On hold.** ...`, saying what exists instead and what it waits on;
  - inline, write "(planned, not built)".
- **Say what enforces a rule.** "A test parses every module" is checkable; "by construction" is
  not.

## Explaining

- **Define project terms on first use** in each document, or link the
  [glossary](glossary.md). Never write a label on its own: not "arm A" but "arm A, the agent with
  no decision model"; not "step 2c" but what step 2c does.
- **Link every reference to another document,** with a section anchor where it helps:
  `[design 010 §5](../designs/010-facts-and-short-answers.md#5-the-answer)`. Link tickets
  (`[#18](https://github.com/BenedatLLC/k8srca/issues/18)`), code files and external projects.
  A bare "§9.8" or "#18" makes the reader search.
- **Always say why.** Every decision and rule carries its reason, ideally the measurement or
  incident behind it, written in the present tense as a reason, not as a story.
- **Use diagrams generously.** Mermaid (which GitHub renders) or ASCII. Good candidates:
  components and data flow, the sequence of a request or a run, decision and state flows,
  pipelines, and a plan's steps. Introduce each diagram with a sentence saying what it shows, keep
  it true to the code, and mark planned parts (dashed lines, "planned").
- **Labels in diagrams and headings are plain English.** "A first pass at decision models", not
  "Thread A: 011 (steps 1–2)".

## Tone

- **Plain, precise and calm.** No slogans, no dramatic one-line paragraphs, no "X, not Y" taglines
  used as conclusions.
- **Words to avoid,** with what to say instead:

  | Avoid | Say |
  | --- | --- |
  | load-bearing | what depends on it |
  | gate, gates, gating | requires, blocks, decides, passes only if |
  | by construction | what makes it so |
  | first-class, robust, seamless, leverage, north star | the specific property |
  | crucially, the point is, the whole game | nothing; state the point |
  | lands (for a change), surface (as a verb) | is merged, ships; shows, reports |
  | bites, earns its keep, the bet | what goes wrong; why it is worth it; the assumption |

- **A heading says what the section is about.** A catchy phrase is allowed only alongside that:
  "k8stools: the only path to the Kubernetes API", not "One door to the cluster".

## History and decisions

- **No change history in the body.** No "Revised 2026-10-03", "earlier versions said", struck-out
  text or dated notes inside a section. Git keeps the history.
- **Keep the reason, drop the story.** A measurement that justifies a choice stays, as a reason.
- **Results go in the plan,** run-by-run logs in [docs/journal/](journal/). A design keeps only
  the results that explain its decisions. If some history matters to a later reader (an approach
  abandoned, a result other documents cite), put it in an "Appendix: history and results" at the
  end of the same document.
- **A decision is recorded where it applies,** with its date and its reason
  ([constitution P9](constitution.md#p9-decisions-are-written-down-where-they-apply)).

## One home for each fact

State a fact in one place and link to it from the others. Status in particular has caused drift
when copied. Each kind of fact has one home:

| Fact | Its home |
| --- | --- |
| What is open, who owns it, what blocks what | GitHub issues and their relationships (sub-issues, "blocked by") |
| The order among work that is not blocked, and why | [todo.md](todo.md), until a project board or a label holds it |
| What a change is and why | Its design |
| How a change is carried out and measured, and what happened | Its plan |
| A design's status | Its front matter and the [design index](../designs/README.md#index) |

A summary elsewhere (README, CLAUDE.md, todo.md) links to the home rather than repeating it. When
a summary and the home disagree, the home wins and the summary is corrected.

## Structure of a design or plan

- Start with front matter ([below](#front-matter-for-designs-and-plans)), then the title heading
  and a paragraph for a newcomer saying what problem it addresses and what to know before reading.
  The templates ([design](../designs/TEMPLATE.md), [plan](../plans/TEMPLATE.md)) have it.
- **Keep section numbers stable** once other documents cite them. Rename a heading's words if you
  must, keep its number, and update the links to it.
- **Keep designs short.** A design says what and why; the steps, their measurements and results
  belong in the plan ([designs/README.md](../designs/README.md#design-plan-and-results)).

## Front matter for designs and plans

Every design and plan starts with YAML front matter, which GitHub shows as a table above the text,
so a reader can see at a glance what the document is, where it stands and what it waits on. The
body follows it with the `#` title heading and the paragraph for a newcomer.

```yaml
---
id: "011"
title: A collector for the first reads, with decision models for what code cannot read
type: change
status: in progress
measured: true
owner: Linda Zhou
issue: 19
plan: plans/011-decision-models.md
built: []
not_built:
  - Judge interface and adapters
  - judge service and its network rule
waits_on: []
replaces: []
related: ["007", "008", "010", "014", "015"]
last_updated: 2026-11-15
---
```

A design's properties:

| Property | Value |
| --- | --- |
| `id` | The design's number, quoted: `"011"` |
| `title` | The title, as in the heading |
| `type` | `change` or `reference` ([two kinds of document](../designs/README.md#two-kinds-of-document)) |
| `status` | One of the [statuses](../designs/README.md#statuses): `draft`, `approved`, `in progress`, `on hold`, `done`, `done in part`, `abandoned`; `reference` for a reference document |
| `measured` | `true` for the one design allowed to spend the live-evaluation budget ([constitution P5](constitution.md#p5-one-measured-design-at-a-time)), otherwise `false` |
| `owner` | The owner's name, or `unassigned` |
| `issue` | The design's parent issue number, or `none` |
| `plan` | The path of its plan, or `none` |
| `built` | What is built, as a short list |
| `not_built` | What is not built, including what is planned, dropped or moved to another design |
| `waits_on` | What blocks it: issues (`"#22"`) or a few words |
| `replaces` | Sections of other designs it replaces (`"008 §3.1"`) |
| `related` | The ids of the designs a reader should know |
| `last_updated` | The date of the last change to its content, `YYYY-MM-DD` |

A plan's properties are `id` and `title` (the design's), `design` (its path), `owner`, `issue`,
`status` (`not started`, `in progress`, `on hold`, `done` or `stopped`) and `last_updated`. A
plan is `on hold` while its design is.

Rules:
- **Plain values only.** Front matter is not Markdown, so a link there does not work; put links in
  the body.
- **Short list items,** a few words each; detail belongs in the body.
- **Change `status` and the [design index](../designs/README.md#index) row together.**
- **Update `last_updated`** whenever the content changes.

## Issues

GitHub issues follow these guidelines too, with a clear one-sentence title. The specifics are in
[development.md, "Writing an issue"](development.md#writing-an-issue).

## Before you finish

- Every link and section anchor resolves.
- Every new project term is defined on first use and added to the [glossary](glossary.md).
- Anything not built is labelled.
- The documents the change affects still agree with each other.
