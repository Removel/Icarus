PYTHON ?= python3
ARGS ?=
APP ?=
REPO_ROOT := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))

.PHONY: install install-dev install-commands install-agent install-gateway install-tui start stop status gateway tui mem0-up mem0-down mem0-logs openkb-up openkb-down openkb-logs test test-agent test-gateway test-tui

install:
	PYTHON="$(PYTHON)" "$(REPO_ROOT)/bin/icarus" install $(APP)

install-dev:
	PYTHON="$(PYTHON)" "$(REPO_ROOT)/bin/icarus" install $(APP) --dev

install-commands:
	"$(REPO_ROOT)/scripts/install-commands.sh"

install-agent:
	PYTHON="$(PYTHON)" "$(REPO_ROOT)/bin/icarus" install agent

install-gateway:
	PYTHON="$(PYTHON)" "$(REPO_ROOT)/bin/icarus" install gateway

install-tui:
	PYTHON="$(PYTHON)" "$(REPO_ROOT)/bin/icarus" install tui

start:
	"$(REPO_ROOT)/bin/icarus" start $(APP) $(ARGS)

stop:
	"$(REPO_ROOT)/bin/icarus" stop $(APP)

status:
	"$(REPO_ROOT)/bin/icarus" status $(APP)

gateway:
	"$(REPO_ROOT)/bin/icarus" start gateway

tui:
	"$(REPO_ROOT)/bin/icarus" tui $(ARGS)

mem0-up:
	"$(REPO_ROOT)/bin/icarus" start mem0

mem0-down:
	"$(REPO_ROOT)/bin/icarus" stop mem0

mem0-logs:
	bash "$(REPO_ROOT)/apps/mem0/scripts/icarus-compose.sh" logs -f

openkb-up:
	"$(REPO_ROOT)/bin/icarus" start openkb

openkb-down:
	"$(REPO_ROOT)/bin/icarus" stop openkb

openkb-logs:
	bash "$(REPO_ROOT)/apps/openkb/scripts/icarus-compose.sh" logs -f

test:
	"$(REPO_ROOT)/scripts/test.sh"

test-agent:
	"$(REPO_ROOT)/apps/agent/scripts/test.sh"

test-gateway:
	"$(REPO_ROOT)/apps/gateway/scripts/test.sh"

test-tui:
	"$(REPO_ROOT)/apps/tui/scripts/test.sh"
