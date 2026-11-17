# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 cockpit_sharehub contributors

VERSION   := $(shell sed -n 's/^__version__ = "\(.*\)"/\1/p' backend/sharehub/__init__.py)
DESTDIR   ?=
PLUGINDIR := $(DESTDIR)/usr/share/cockpit/sharehub
LIBDIR    := $(DESTDIR)/usr/lib/sharehub
UNITDIR   := $(DESTDIR)/usr/lib/systemd/system

.PHONY: all install uninstall test lint deb dist clean

all:
	@echo "Targets: install, uninstall, test, lint, deb, dist, clean (version $(VERSION))"

install:
	install -d $(PLUGINDIR)/css $(PLUGINDIR)/js $(LIBDIR)/sharehub $(UNITDIR)
	install -m 0644 frontend/manifest.json frontend/index.html $(PLUGINDIR)/
	install -m 0644 frontend/css/*.css $(PLUGINDIR)/css/
	install -m 0644 frontend/js/*.js $(PLUGINDIR)/js/
	install -m 0644 backend/sharehub/*.py $(LIBDIR)/sharehub/
	install -m 0755 backend/sharehub-helper $(LIBDIR)/sharehub-helper
	install -m 0644 systemd/sharehub-recycle.service systemd/sharehub-recycle.timer $(UNITDIR)/

uninstall:
	rm -rf $(PLUGINDIR) $(LIBDIR)
	rm -f $(UNITDIR)/sharehub-recycle.service $(UNITDIR)/sharehub-recycle.timer

test:
	python3 -m pytest -q tests/

lint:
	python3 -m pyflakes backend tests
	for f in frontend/js/*.js; do node --check $$f || exit 1; done
	bash -n install.sh uninstall.sh

deb:
	dpkg-buildpackage -us -uc -b
	mkdir -p dist && mv ../cockpit-sharehub_$(VERSION)_all.deb dist/

dist:
	mkdir -p dist
	git archive --format=tar.gz --prefix=cockpit-sharehub-$(VERSION)/ -o dist/cockpit-sharehub-$(VERSION).tar.gz HEAD

clean:
	rm -rf dist debian/cockpit-sharehub debian/.debhelper debian/files debian/*.substvars debian/debhelper-build-stamp debian/*.debhelper debian/*.debhelper.log
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
