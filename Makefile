PYTHON := python3

.PHONY: all fetch freedict gcide check

all: freedict gcide check

fetch:
	bash scripts/fetch_sources.sh

freedict: fetch
	$(PYTHON) scripts/build_freedict_mdx.py

gcide: fetch
	$(PYTHON) scripts/build_gcide_mdx.py

# The rebuilt files must be byte-identical to the ones OpenMDict ships.
check:
	shasum -a 256 -c SHA256SUMS
