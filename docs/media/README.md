# Meet Quack

These are captures of Quack's actual interface. The ink and test accounts are synthetic, and a local fake Meta server supplies scripted readings. The notebook, API, mathematical verifier and learning flow are real. The images demonstrate product states, not live Meta recognition, physical Apple Pencil accuracy or educational outcomes.

The hero and mascot export use the original SVG duck from `apps/web/components/duck/Duck.tsx`. The redesigned heroes reuse the app's Bricolage Grotesque and Figtree typography as outlined paths, with self-contained crops of the actual UI. Desktop and narrow-screen compositions each have light and dark versions. The product UI retains its original cream appearance in both themes.

The detail images crop empty canvas space to keep working and feedback readable. They do not replace text, ink or controls. `feedback-detail.webp` is the top of `mistake-hint.webp`; the question and missing-letter feedback are also visible in `mistake-feedback.webp`. `hint-detail.webp` is the lower-right hint and duck from the same capture. Notebook, guided-learning, correction and practice details come from their corresponding full captures. Full-frame originals are preserved below.

| Image | What it shows |
|---|---|
| [Hero](hero.svg) · [Dark](hero-dark.svg) | Original duck, app typography and actual feedback and hint details |
| [Narrow hero](hero-mobile.svg) · [Dark](hero-mobile-dark.svg) | Stacked composition for small screens |
| [Guided canvas detail](learning-detail.webp) | Question, controls and working area from the guided learning capture |
| [Notebook detail](notebook-detail.webp) | Toolbar and handwritten arithmetic mistake |
| [Feedback detail](feedback-detail.webp) · [Hint detail](hint-detail.webp) | Missing-variable feedback, then requested help |
| [Correction detail](correction-detail.webp) · [Practice detail](practice-detail.webp) | Verified `5x`, then the related `6n − 2n` question |
| [Social preview](social-preview.png) · [SVG source](social-preview.svg) | Existing 1280 × 640 sharing artwork, retained after visual review |
| [Duck](quack.svg) | The original mascot artwork |
| [Six learning paths](learning-paths.webp) | The available course catalog |
| [My Notebook](notebook.webp) | Freeform working and gentle mistake feedback |
| [Guided learning](active-learning.webp) | A question above the reusable handwriting canvas |
| [Mistake feedback](mistake-feedback.webp) | The dropped variable in `3x + 2x = 5` |
| [Helpful hint](mistake-hint.webp) | Requested help on the supported mistake |
| [Verified correction](correction.webp) | The accepted answer `5x` |
| [Algebra roadmap](algebra-roadmap.webp) | Connected levels and progression |
| [Related practice](adaptive-follow-up.webp) | A follow-up on combining like terms |
| [Level Mastered](level-mastered.webp) | The duck's mastery celebration |
| [My Mistakes](my-mistakes.webp) | Corrected and open mistakes in a synthetic account |
| [Welcome](welcome.webp) | The welcome screen and companion |

<img src="adaptive-follow-up.webp" width="400" alt="A related combining-like-terms question after a verified correction." />
<img src="level-mastered.webp" width="400" alt="Quack celebrates a mastered level." />

The original product images are full-frame WebP conversions from desktop and tablet-sized browser viewports, with no replacement UI. The reusable synthetic capture driver is `apps/web/tests/e2e/visual.lessons.spec.ts`; screenshots are opt-in as described in [testing](../TESTING.md).
