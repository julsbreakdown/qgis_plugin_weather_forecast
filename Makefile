.PHONY: test lint smoke zip install release

VERSION := $(shell sed -n 's/^version=//p' qgis/wxforecast_qgis/metadata.txt)
PLUGINS ?= $(HOME)/.local/share/QGIS/QGIS4/profiles/default/python/plugins

test:
	uv run pytest -m 'not network'

lint:
	uv run ruff check && uv run ruff format --check src tests qgis

smoke:
	QT_QPA_PLATFORM=offscreen PYTHONPATH=/usr/share/qgis/python python3 qgis/smoke_test.py

zip:
	python3 qgis/build_zip.py

# Replaces the installed plugin; a dev symlink is removed as a link, the checkout is untouched.
install: zip
	rm -rf "$(PLUGINS)/wxforecast_qgis"
	unzip -q -o dist/wxforecast_qgis-$(VERSION).zip -d "$(PLUGINS)"

# make release V=0.2.0: CI tests, builds the zip and publishes the GitHub release.
release:
	test -n "$(V)" && git tag -a v$(V) -m v$(V) && git push origin v$(V)
