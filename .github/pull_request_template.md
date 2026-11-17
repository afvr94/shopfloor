## What and why

<!-- What this changes, and the problem it solves. Link the issue if there is one. -->

## How it's tested

<!-- The test that fails without this change, and anything you ran by hand. -->

## Checklist

- [ ] The title follows Conventional Commits (`fix(guard): ...`).
- [ ] Tests pass with a throwaway home: `HOME=$(mktemp -d) SHOPFLOOR_HOME=$(mktemp -d)/sf python3 -m unittest`
- [ ] New config keys are in `docs/config.md`.
- [ ] Changes to what a crew can do are in `docs/SAFETY.md`, with guard rows in `engine/hooks/test_guard.py` if the guard changed.
