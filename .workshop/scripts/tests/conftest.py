import sys
from pathlib import Path

# The workshop engine moved under `.workshop/`, so the `scripts` package now
# lives at `.workshop/scripts`. Put `.workshop/` on sys.path so `import
# scripts.advance_step` resolves regardless of the working directory. This lets
# maintainers run `python -m pytest .workshop/scripts/tests` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
