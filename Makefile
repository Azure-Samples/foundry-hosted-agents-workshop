# Convenience aliases for the fully-local workshop flow.
#
# These wrap .workshop/scripts/advance_step.py and .workshop/scripts/preflight.py so participants
# running locally (no GitHub Actions round-trip) can move between steps with a
# single command. The Action-driven flow keeps working unchanged.
#
# Override the Python interpreter when needed:
#   PYTHON=python3 make advance

PYTHON ?= python

.PHONY: help advance reset reset-current preflight sync-template

help:
	@echo "Workshop targets (fully-local flow):"
	@echo "  make advance        Advance to the next workshop step and auto-commit"
	@echo "  make reset          Reset the workshop to step 0 and auto-commit"
	@echo "  make reset-current  Re-lay the CURRENT step's clean starter files and"
	@echo "                      re-render its README, then auto-commit (backs up"
	@echo "                      travel_assistant/ first). Pairs with sync-template."
	@echo "  make preflight      Run environment preflight checks"
	@echo "  make sync-template  Pull latest .workshop/ and .github/ from the upstream"
	@echo "                      template and commit, without advancing the step"
	@echo "                      (review, then 'git push' yourself). Unlike the CI"
	@echo "                      workflow, a local sync also updates .github/workflows/"
	@echo ""
	@echo "If 'make' is unavailable, run the scripts directly, e.g.:"
	@echo "  $(PYTHON) .workshop/scripts/advance_step.py --expected-current-step 0 --auto-commit"
	@echo "  $(PYTHON) .workshop/scripts/advance_step.py --reset --auto-commit"
	@echo "  $(PYTHON) .workshop/scripts/advance_step.py --reset-current --auto-commit"
	@echo "  $(PYTHON) .workshop/scripts/preflight.py"
	@echo "  $(PYTHON) .workshop/scripts/sync_template.py --auto-commit   # add --push to push too"

advance:
	$(PYTHON) .workshop/scripts/advance_step.py --auto-commit

reset:
	$(PYTHON) .workshop/scripts/advance_step.py --reset --auto-commit

reset-current:
	$(PYTHON) .workshop/scripts/advance_step.py --reset-current --auto-commit

preflight:
	$(PYTHON) .workshop/scripts/preflight.py

sync-template:
	$(PYTHON) .workshop/scripts/sync_template.py --auto-commit
