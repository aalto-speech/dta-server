# Future plan — learner-facing feedback

Design notes for work we have decided **not** to do yet, kept here so the reasoning
survives the decision. Nothing in this document is implemented. See
[API.md](./API.md) for what the server actually sends today.

## Cohort comparison: rank is the secondary number

`POST /analytics/comparison` returns the learner's position within their CEFR cohort,
and the app displays it. That feature stays. This section records what the feedback
literature says about it, so the next person to touch it knows what they are trading.

### The evidence

- **Kluger & DeNisi (1996)**, *Psychological Bulletin* — the standard meta-analysis of
  feedback interventions. Roughly a third of them **reduce** subsequent performance. The
  interventions that hurt are those directing attention toward the *self* rather than the
  *task*. A normative rank is the canonical self-directed feedback.
- **Hattie & Timperley (2007)**, *Review of Educational Research* — of the four feedback
  levels (task, process, self-regulation, self), the **self** level is the least
  effective. "Where to next" outperforms "where you are."
- **Self-Determination Theory** (Deci & Ryan) — normative comparison tends to undermine
  intrinsic motivation and shifts learners from mastery goals toward performance goals.
  Performance-*avoidance* goals predict withdrawal from the task.
- **Foreign language anxiety** (Horwitz, Horwitz & Cope 1986, *Modern Language Journal*)
  is highest for **speaking** specifically, and peer comparison is a documented trigger.
  Through MacIntyre's *Willingness to Communicate*, anxiety suppresses exactly the
  behaviour this app depends on: that someone is willing to record themselves talking.
  For adult immigrants learning Finnish, the failure mode is not churn from an app — it
  is less speaking practice.
- **Prospect theory** (Kahneman & Tversky) — a displayed rank that can fall turns every
  new signup into a small loss event, felt roughly twice as strongly as the equivalent
  gain. The learner did nothing wrong and is told they slipped.
- **CEFR is a criterion-referenced framework.** Its design intent is describing what a
  learner *can do*, not where they sit against peers. A percentile display quietly
  reintroduces the norm-referencing that CEFR exists to replace.

### What follows from it

Keep the comparison — it is built, and learners do ask for it. Treat it as the
**secondary** number, and prefer criterion-referenced information (the CEFR band and the
score) as the primary one. The current display rules already lean this way:

- nothing is shown below the 50th percentile — a learner in the bottom half sees their
  score and band, and no ranking at all
- rungs are coarse, so the number does not twitch
- claims round in the conservative direction, so no displayed statement is ever false

## Deferred items

### 1. Self-referenced progress as the primary display

The form the feedback literature actually supports: compare the learner to their own
past rather than to peers. *"Your last five recordings averaged 2.1, up from 1.8."*

Needs no cohort, no `MIN_COHORT_SIZE`, and works for levels whose cohort is too small to
compare at all. Not possible at this stage — the endpoint has no per-user time series and
the app has no surface for it. Revisit when either exists.

### 2. Ratchet the displayed band so it never falls

A learner's rung can drop because other people joined, not because they got worse.
Two standard mitigations, either is enough:

- **ratchet** — display the best rung achieved, never a lower one
- **hysteresis** — require crossing back past the boundary by a margin (≥1 SEM) before
  showing a worse rung

Removes an entire category of "why did I get worse?" support questions, and defuses the
prospect-theory objection above.

### 3. Goal gradient

*"Two places from the top 10"* motivates better than any static position: effort rises as
a goal comes within reach (Kivetz, Urminsky & Zheng 2006, *JMR*). Cheap to compute from
the same rank — it is the distance to the next rung.

### 4. Set the rung count from person separation reliability

The number of display states should be capped by the number of statistically
distinguishable performance levels, which the MFRM output gives directly (Wright &
Masters' strata formula, `strata = (4G+1)/3`, `G = sqrt(R/(1-R))`):

| Person separation reliability | Distinguishable levels |
| ----------------------------- | ---------------------- |
| 0.80                          | ~3                     |
| 0.90                          | ~4.3                   |
| 0.95                          | ~6                     |

A typical speaking assessment supports **3–5 real levels**. Any ladder with more rungs
than that is displaying measurement error as achievement. The current ladders are short
for this reason; when the reliability figure is known, it should be recorded here and the
ladders checked against it.

Related: never display a distinction smaller than ~1 SEM. This is also why rank ties are
resolved by competition rank — two learners whose scores differ by less than the standard
error are not meaningfully ordered, so they share a rank rather than being split by an
arbitrary tiebreak.

### 5. Downward comparison for the bottom half

If the bottom half should ever see something, the supported form is a downward
comparison expressed as a **count of people behind them** — *"ahead of 30 learners at
your level"* — not a percentile. Downward comparison protects self-evaluation (Wills
1981), the number grows over time rather than shrinking, and it never states a position
the learner would read as a verdict. Currently we show nothing instead, which is the
safer default.
