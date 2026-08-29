PYTHON ?= python3
ARGS ?=
REPO_ROOT := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))

.PHONY: install install-dev install-commands install-agent install-gateway install-tui start gateway tui test test-agent test-gateway test-tui

install:
	PYTHON="$(PYTHON)" "$(REPO_ROOT)/scripts/install.sh"

install-dev:
	PYTHON="$(PYTHON)" "$(REPO_ROOT)/scripts/install.sh" --dev

install-commands:
	"$(REPO_ROOT)/scripts/install-commands.sh"

install-agent:
	PYTHON="$(PYTHON)" "$(REPO_ROOT)/apps/agent/scripts/install.sh"

install-gateway:
	PYTHON="$(PYTHON)" "$(REPO_ROOT)/apps/gateway/scripts/install.sh"

install-tui:
	PYTHON="$(PYTHON)" "$(REPO_ROOT)/apps/tui/scripts/install.sh"

start:
	"$(REPO_ROOT)/scripts/start.sh" $(ARGS)

gateway:
	"$(REPO_ROOT)/apps/gateway/scripts/start.sh" $(ARGS)

tui:
	"$(REPO_ROOT)/apps/tui/scripts/start.sh" $(ARGS)

test:
	"$(REPO_ROOT)/scripts/test.sh"

test-agent:
	"$(REPO_ROOT)/apps/agent/scripts/test.sh"

test-gateway:
	"$(REPO_ROOT)/apps/gateway/scripts/test.sh"

test-tui:
	"$(REPO_ROOT)/apps/tui/scripts/test.sh"
