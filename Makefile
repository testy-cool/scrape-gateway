UV ?= uv

.PHONY: check lint format-check fmt test contract-check docs-check smoke install package-check image-check live-check

install:
	$(UV) sync --all-extras

check: format-check lint contract-check docs-check test smoke

lint:
	$(UV) run ruff check .

format-check:
	$(UV) run ruff format --check .

fmt:
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

contract-check:
	$(UV) run python scripts/validate_provider_contracts.py
	$(UV) run pytest -q tests/test_provider_contracts.py

docs-check:
	$(UV) run python scripts/check_docs_links.py

test:
	$(UV) run pytest -q -m "not live"

smoke:
	$(UV) run sgw --help >/dev/null

package-check:
	UV=$(UV) scripts/package-check.sh

image-check:
	scripts/image-check.sh

live-check:
	@test "$${ALLOW_LIVE:-}" = 1 || { echo "set ALLOW_LIVE=1 to authorize external/provider calls" >&2; exit 2; }
	$(UV) run pytest -q -m live
	$(UV) run python examples/basic.py
