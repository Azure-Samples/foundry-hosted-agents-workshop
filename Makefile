# Convenience aliases for the fully-local workshop flow.
#
# These wrap .workshop/scripts/advance_step.py and .workshop/scripts/preflight.py so participants
# running locally (no GitHub Actions round-trip) can move between steps with a
# single command. The Action-driven flow keeps working unchanged.
#
# Override the Python interpreter when needed:
#   PYTHON=python3 make advance

PYTHON ?= python

.PHONY: help advance reset preflight

help:
	@echo "Workshop targets (fully-local flow):"
	@echo "  make advance    Advance to the next workshop step and auto-commit"
	@echo "  make reset      Reset the workshop to step 0 and auto-commit"
	@echo "  make preflight  Run environment preflight checks"
	@echo ""
	@echo "If 'make' is unavailable, run the scripts directly, e.g.:"
	@echo "  $(PYTHON) .workshop/scripts/advance_step.py --expected-current-step 0 --auto-commit"
	@echo "  $(PYTHON) .workshop/scripts/advance_step.py --reset --auto-commit"
	@echo "  $(PYTHON) .workshop/scripts/preflight.py"

advance:
	$(PYTHON) .workshop/scripts/advance_step.py --auto-commit

reset:
	$(PYTHON) .workshop/scripts/advance_step.py --reset --auto-commit

preflight:
	$(PYTHON) .workshop/scripts/preflight.py
