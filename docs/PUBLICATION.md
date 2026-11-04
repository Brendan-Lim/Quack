# Public-file policy

The public source set consists of application code, required runtime prompts, automated tests, synthetic fixtures, curated product media and the public documentation index.

Every approved path is listed literally in `scripts/public-files.txt`. A new file is rejected until that manifest is deliberately reviewed and updated. Independent checks reject development-agent instructions, owner working records, databases, credentials, runtime ledgers, private traces and generated evaluation results. Versioned application prompts under `services/api/quack/prompts/` are allowed runtime resources.

Before every push, stage intended changes, then run:

```bash
./scripts/check-publication.sh --history
git diff --cached --check
```

The guard checks staged paths and content, public documentation links, SVG syntax and all reachable commits. Use a separate secrets scanner as well; filename and token-pattern checks cannot prove that arbitrary private data is absent.

Enable the included local pre-push hook in each new checkout:

```bash
git config core.hooksPath .githooks
```

The hook requires a clean checkout and validates history before pushing. It is a local safeguard and must be enabled for each clone. Regenerated `AGENTS.md` and `CLAUDE.md` files are ignored and rejected if tracked.

When a reviewed application input is added to the manifest, regenerate the Docker context policy:

```bash
python3 scripts/check_publication.py --write-dockerignore
```

The root `.dockerignore` starts with everything excluded and permits only declared runtime source and configuration inputs. It excludes documentation, test outputs, databases and working directories from Docker packaging. Hosting tools that do not honour `.dockerignore` must package only their declared frontend/backend source directories. Run the public-file guard before packaging and keep owner working records outside the repository.
