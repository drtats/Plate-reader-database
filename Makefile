UV ?= uv
UV_RUN = $(UV) run --no-sync

.PHONY: setup format lint typecheck test test-unit check run-local run-standalone build-standalone seed-demo benchmark benchmark-workflows migrate import-legacy-growth-dry-run import-legacy-mic-dry-run

setup:
	$(UV) sync --all-groups

format:
	$(UV_RUN) ruff format .

lint:
	$(UV_RUN) ruff format --check .
	$(UV_RUN) ruff check .

typecheck:
	$(UV_RUN) mypy src app.py

test:
	$(UV_RUN) pytest

test-unit:
	$(UV_RUN) pytest tests/unit

check: lint typecheck test

run-local:
	PLATE_READER_ENV=development PLATE_READER_STORAGE_MODE=fake-cloud $(UV_RUN) streamlit run app.py

run-standalone:
	$(UV_RUN) python -m plate_reader.standalone run

build-standalone:
	$(UV_RUN) python scripts/build_standalone.py

seed-demo:
	$(UV_RUN) python scripts/seed_demo.py demo.sqlite --backend fake-cloud

benchmark:
	$(UV_RUN) python scripts/benchmark_persistence.py

benchmark-workflows:
	$(UV_RUN) python scripts/benchmark_workflows.py

migrate:
	$(UV_RUN) python scripts/migrate_database.py .data/plate-reader.sqlite --backend fake-cloud

import-legacy-growth-dry-run:
	$(UV_RUN) python scripts/import_legacy_growth.py .data/plate-reader.sqlite tests/fixtures/legacy/growth_v4.sqlite --backend fake-cloud

import-legacy-mic-dry-run:
	$(UV_RUN) python scripts/import_legacy_mic.py .data/plate-reader.sqlite tests/fixtures/legacy/mic_legacy.sqlite --backend fake-cloud
