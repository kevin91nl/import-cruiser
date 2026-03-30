SELF_GRAPH_DIR := artifacts/self-graph
SELF_GRAPH_BASE := $(SELF_GRAPH_DIR)/import-cruiser-self-graph
SELF_GRAPH_INCLUDE := src/import_cruiser/
SELF_GRAPH_EXCLUDE := __init__\.py$$|/tests/

.PHONY: self-graph
self-graph:
	@mkdir -p $(SELF_GRAPH_DIR)
	PYTHONPATH=src python3 -m import_cruiser.cli export . --format dot --show-loc --style depcruise --edge-mode auto --include-external-deps --cluster-mode module --cluster-depth 1 --include-path '$(SELF_GRAPH_INCLUDE)' --exclude-path '$(SELF_GRAPH_EXCLUDE)' --output $(SELF_GRAPH_BASE).dot
	PYTHONPATH=src python3 -m import_cruiser.cli export . --format html --show-loc --style depcruise --edge-mode auto --include-external-deps --cluster-mode module --cluster-depth 1 --include-path '$(SELF_GRAPH_INCLUDE)' --exclude-path '$(SELF_GRAPH_EXCLUDE)' --output $(SELF_GRAPH_BASE).html
	PYTHONPATH=src python3 -m import_cruiser.cli export . --format svg --show-loc --style depcruise --edge-mode auto --include-external-deps --cluster-mode module --cluster-depth 1 --include-path '$(SELF_GRAPH_INCLUDE)' --exclude-path '$(SELF_GRAPH_EXCLUDE)' --output $(SELF_GRAPH_BASE).svg
