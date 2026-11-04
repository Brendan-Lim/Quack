# Credits and licensing

Quack is owned by its respective contributors. No project-wide open-source license is granted. Public visibility does not grant an additional license; third-party components retain their own licenses.

## Artwork and type

The yellow duck is an original project SVG in `apps/web/components/duck/Duck.tsx`. The release mascot export and hero composition reuse those exact paths. All product screenshots are captures of the current app with synthetic test accounts and ink; see [media provenance](media/README.md). No stock duck or private QA video frame was introduced.

The app uses Figtree and Bricolage Grotesque via Next's Google Fonts integration. Their upstream SIL Open Font License notices are preserved in [Figtree-OFL.txt](licenses/Figtree-OFL.txt) and [BricolageGrotesque-OFL.txt](licenses/BricolageGrotesque-OFL.txt), retrieved from the Google Fonts repository. The banner uses system font fallbacks, rather than redistributing font files.

Lucide supplies UI icons. The unused starter SVGs in `apps/web/public/` retain their original shapes; Next.js and Vercel marks identify their respective owners and are not Quack-owned marks.

## Dependencies

Direct runtime dependencies and the versions inspected locally are listed below. Python requirements declare supported ranges; this table records inspected dependency versions, rather than a new lockfile. Third-party licenses remain with their owners. [Collected package notices](licenses/THIRD_PARTY_NOTICES.txt) preserve the available license/copyright files from these installed distributions; dependency declarations and upstream package metadata govern each distribution. This is not a claim that Quack owns or relicenses them.

| Package | Inspected version | Declared license |
|---|---|---|
| @supabase/supabase-js | 2.118.0 | MIT |
| idb-keyval | 6.3.0 | Apache-2.0 |
| lucide-react | 1.65.0 | ISC |
| next | 16.4.3 | MIT |
| perfect-freehand | 1.2.3 | MIT |
| react | 19.3.0 | MIT |
| react-dom | 19.3.0 | MIT |
| fastapi | 0.162.0 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |
| pydantic | 2.14.2 | MIT |
| pydantic-settings | 2.16.2 | MIT |
| openai | 2.54.0 | Apache-2.0 |
| httpx | 0.28.1 | BSD-3-Clause |
| sympy | 1.14.0 | BSD |
| pillow | 13.0.0 | MIT-CMU |
| python-multipart | 0.0.32 | Apache-2.0 |
| pyjwt | 2.15.1 | MIT |
| psycopg | 3.3.6 | LGPL-3.0-only |
| psycopg-binary | 3.3.6 | LGPL-3.0-only |

## AI and development

Meta provides the Muse Spark multimodal API used for recognition and generated language. Its service terms govern API use; Meta is not claimed as a project contributor or endorser. The application performs symbolic verification in Python with SymPy. No custom model training or fine-tuning is claimed.

Development was AI-assisted, including source work, tests, documentation and release preparation with Codex. Synthetic screenshot generation scripts exercise the actual interface; they do not measure real handwriting accuracy.
