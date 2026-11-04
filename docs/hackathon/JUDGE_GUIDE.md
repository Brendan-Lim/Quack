# Try Quack

Quack is a handwriting-first maths companion. It helps students notice supported mistakes, ask for hints, correct their own work and continue practising.

**[Open Quack](https://quack-amber-seven.vercel.app)** · **[Source](https://github.com/Brendan-Lim/Quack)**

## Recommended five-minute route

1. Open the public demo and create your own account. Registration signs you in immediately; no shared judge credentials are published. The backend may take a moment to wake after inactivity.
2. Complete the short handwriting calibration, then choose **Learn**. Samples supply writer-specific context; improved accuracy has not been demonstrated.
3. Browse Arithmetic Foundations, Fractions & Decimals, Algebra Foundations, Geometry, Trigonometry and Calculus. All six are playable; Algebra has seven levels including its challenge and the deepest progression.
4. Open Algebra's first level, **Meet the Variables**. Read the lesson and example, then try the first question. For **Simplify `y + y + y + y + y`**, write `5` and pause. Quack can notice the missing letter. Correct it to `5y`, wait for verification, then advance to the related question. A correction earns assisted credit; an independent answer earns more.
5. Continue through Algebra to **Combine Like Terms** for another example: **Simplify `3x + 2x`**. Try `5`, ask for a hint if the mistake is verified, then correct it to `5x`. Your learning progress is saved to your account.
6. Switch to **My Notebook** to write your own question. Try pen/eraser, undo/redo and circling supported working. My Mistakes and session history are in the account menu after verified learning events.

## What requires the model?

Recognition and generated help require a backend Meta Model API key and available credit. With no key, the interface opens but cannot read ink. Health includes `modelConfigured`; this indicates configuration, not a successful live request.

The public demo has live Meta recognition enabled with a limited shared allowance. A hosted smoke test used synthetic pointer-drawn ink: Meta read `5`, SymPy rejected the missing variable, and the correction to `5y` was accepted before a targeted follow-up loaded. This is a narrow integration check, not an accuracy benchmark. The gallery below still uses scripted recognition as disclosed.

Parsing, symbolic verification, question generation, misconception rules, next-question selection and mastery credit are deterministic. Recognition supplies the mathematical text they act on. Two readings can agree on a wrong interpretation.

## What to expect

Quack can ask you to rewrite unclear work or clarify intent. In the notebook, `3x + 2x = 5` needs simplifying/solving context. Geometry has numerical word problems. Some wrong trigonometric exact values remain uncertain. Physical Apple Pencil accuracy is not established by synthetic tests. Learning records are stored in PostgreSQL; notebook pages remain on your device. Password-reset email delivery has not been verified.

Gallery images use synthetic strokes and scripted recognition, not live model performance. There is no controlled learner-outcome study. See [technical overview](TECHNICAL_OVERVIEW.md), [testing](../TESTING.md) and [limitations](../../README.md#current-limits).
