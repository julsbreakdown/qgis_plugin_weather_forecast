.PHONY: test lint smoke zip release

test:
	uv run pytest -m 'not network'

lint:
	uv run ruff check && uv run ruff format --check src tests qgis

smoke:
	QT_QPA_PLATFORM=offscreen PYTHONPATH=/usr/share/qgis/python python3 qgis/smoke_test.py

zip:
	python3 qgis/build_zip.py

# make release V=0.2.0: CI tests, builds the zip and publishes the GitHub release.
release:
	test -n "$(V)" && git tag -a v$(V) -m v$(V) && git push origin v$(V)
