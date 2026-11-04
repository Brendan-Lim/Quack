# Try Quack

Quack is a handwriting-first maths companion. It helps students notice supported mistakes, ask for hints, correct their own work and continue practising.

**Source:** [Brendan-Lim/Quack](https://github.com/Brendan-Lim/Quack). **Public demo:** pending deployment.

## Recommended five-minute route

1. Open the app. On a hosted build, create your own account using configured Supabase sign-in. The source release has no shared credentials. If hosting is pending, follow the README's local setup; labelled local accounts are for development only.
2. Complete the short handwriting calibration, then choose **Learn**. Samples supply writer-specific context; improved accuracy has not been demonstrated.
3. Browse Arithmetic Foundations, Fractions & Decimals, Algebra Foundations, Geometry, Trigonometry and Calculus. All six are playable; Algebra has seven levels including its challenge and the deepest progression.
4. Open Algebra's first available level. Read the lesson and example, then write your answer on the canvas. Observe checking and acceptance. Continue until the next level opens.
5. In **Combine Like Terms**, try **Simplify `3x + 2x`**. Write `5`, pause, and open the first hint if the mistake is verified. Add `x` to make `5x`. Wait for a verified correction, then advance to related practice.
6. Switch to **My Notebook** to write your own question. Try pen/eraser, undo/redo and circling supported working. My Mistakes and session history are in the account menu after verified learning events.

## What requires the model?

Recognition and generated help require a backend Meta Model API key and available credit. With no key, the interface opens but cannot read ink. Health includes `modelConfigured`; this indicates configuration, not a successful live request.

Parsing, symbolic verification, question generation, misconception rules, next-question selection and mastery credit are deterministic. Recognition supplies the mathematical text they act on. Two readings can agree on a wrong interpretation.

## What to expect

Quack can ask you to rewrite unclear work or clarify intent. In the notebook, `3x + 2x = 5` needs simplifying/solving context. Geometry has numerical word problems. Some wrong trigonometric exact values remain uncertain. Physical Apple Pencil accuracy is not established by synthetic tests; production authentication needs a correctly configured Supabase project.

Gallery images use synthetic strokes and scripted recognition, not live model performance. There is no controlled learner-outcome study. See [technical overview](TECHNICAL_OVERVIEW.md), [testing](../TESTING.md) and [limitations](../../README.md#current-limits).
