.PHONY: download-zenohc build run test clean help tidy lint \
        bridge-build bridge-run bridge-clean

TUNNEL_DIR=tunnel
BINARY_CLIENT=$(shell pwd)/bin/tunnel
BINARY_ENTRY=./cmd

# --- Bridge (ROS2) ---
BRIDGE_DIR=bridge
ROS_DISTRO?=humble
ROBOT?=g1
BRIDGE_PKG=isaac_sim_bridge_$(ROBOT)
RMW_IMPLEMENTATION?=rmw_cyclonedds_cpp

ZENOH_C_VERSION=1.9.0
ZENOH_C_DIR=.zenoh-c
ZENOH_C_ABS_DIR=$(shell pwd)/$(ZENOH_C_DIR)
UNAME_S := $(shell uname -s)
UNAME_M := $(shell uname -m)

ifeq ($(UNAME_S),Linux)
	ifeq ($(UNAME_M),x86_64)
		ZENOH_PLATFORM=x86_64-unknown-linux-gnu
	else ifeq ($(UNAME_M),aarch64)
		ZENOH_PLATFORM=aarch64-unknown-linux-gnu
	endif
	DYLD_VAR=LD_LIBRARY_PATH
else ifeq ($(UNAME_S),Darwin)
	ifeq ($(UNAME_M),arm64)
		ZENOH_PLATFORM=aarch64-apple-darwin
	else
		ZENOH_PLATFORM=x86_64-apple-darwin
	endif
	DYLD_VAR=DYLD_LIBRARY_PATH
endif

ZENOH_URL=https://github.com/eclipse-zenoh/zenoh-c/releases/download/$(ZENOH_C_VERSION)/zenoh-c-$(ZENOH_C_VERSION)-$(ZENOH_PLATFORM)-standalone.zip

# CGO_LDFLAGS is applied to every cgo package, so keep it to the search path
# only — zenoh-go's own "#cgo LDFLAGS: -lzenohc" links the library, and the
# rpath is injected once at the final link via GO_LDFLAGS to avoid duplicate
# ld warnings.
export CGO_ENABLED=1
export CGO_CFLAGS=-I$(ZENOH_C_ABS_DIR)/include
export CGO_LDFLAGS=-L$(ZENOH_C_ABS_DIR)/lib
GO_LDFLAGS=-ldflags "-extldflags '-Wl,-rpath,$(ZENOH_C_ABS_DIR)/lib'"

.DEFAULT_GOAL := help

help:
	@echo "Available targets:"
	@echo "Tunnel (Go):"
	@echo "  build          - Build the tunnel client binary"
	@echo "  run            - Run the tunnel client"
	@echo "  test           - Run tests"
	@echo "  test-coverage  - Run tests with coverage report"
	@echo "  lint           - Run linter"
	@echo "  download-zenohc - Download and extract zenoh-c library"
	@echo "  tidy           - Tidy and verify Go modules"
	@echo "Bridge (ROS2):"
	@echo "  bridge-build   - colcon build the ROS2 bridge workspace"
	@echo "  bridge-run     - Launch the bridge adapter (ROBOT=g1|go2|tron1, default g1)"
	@echo "  bridge-clean   - Remove bridge build/install/log dirs"
	@echo "Common:"
	@echo "  clean          - Remove all build artifacts (tunnel + bridge)"
	@echo "  help           - Display this help message"

build: download-zenohc
	@mkdir -p bin
	cd $(TUNNEL_DIR) && $(DYLD_VAR)=$(ZENOH_C_ABS_DIR)/lib go build $(GO_LDFLAGS) -o $(BINARY_CLIENT) $(BINARY_ENTRY)

run: download-zenohc
	cd $(TUNNEL_DIR) && $(DYLD_VAR)=$(ZENOH_C_ABS_DIR)/lib go run $(GO_LDFLAGS) $(BINARY_ENTRY)

download-zenohc:
	@echo "Downloading zenoh-c $(ZENOH_C_VERSION) for $(ZENOH_PLATFORM)..."
	@mkdir -p $(ZENOH_C_DIR)
	@if [ ! -f "$(ZENOH_C_DIR)/lib/libzenohc.dylib" ] && [ ! -f "$(ZENOH_C_DIR)/lib/libzenohc.so" ]; then \
		echo "Fetching $(ZENOH_URL)..."; \
		curl -sSL -o /tmp/zenoh-c.zip $(ZENOH_URL); \
		unzip -q /tmp/zenoh-c.zip -d $(ZENOH_C_DIR); \
		rm /tmp/zenoh-c.zip; \
		echo "zenoh-c installed to $(ZENOH_C_DIR)"; \
		if [ "$(UNAME_S)" = "Darwin" ]; then \
			echo "Patching dylib install names..."; \
			if [ -f "$(ZENOH_C_ABS_DIR)/lib/libzenohc.dylib" ]; then \
				install_name_tool -id "@rpath/libzenohc.dylib" "$(ZENOH_C_ABS_DIR)/lib/libzenohc.dylib"; \
			fi; \
		fi; \
	else \
		echo "zenoh-c already installed in $(ZENOH_C_DIR)"; \
	fi

test: download-zenohc
	cd $(TUNNEL_DIR) && $(DYLD_VAR)=$(ZENOH_C_ABS_DIR)/lib go test $(GO_LDFLAGS) -p 8 -v ./...

test-coverage: download-zenohc
	cd $(TUNNEL_DIR) && $(DYLD_VAR)=$(ZENOH_C_ABS_DIR)/lib go test $(GO_LDFLAGS) -p 8 -v -coverprofile=coverage.out ./...

lint: download-zenohc
	cd $(TUNNEL_DIR) && $(DYLD_VAR)=$(ZENOH_C_ABS_DIR)/lib golangci-lint run --timeout=5m

tidy:
	cd $(TUNNEL_DIR) && go mod tidy
	cd $(TUNNEL_DIR) && go mod verify

bridge-build:
	cd $(BRIDGE_DIR) && . /opt/ros/$(ROS_DISTRO)/setup.sh && colcon build --symlink-install

bridge-run:
	cd $(BRIDGE_DIR) && . /opt/ros/$(ROS_DISTRO)/setup.sh && . install/setup.sh && \
		RMW_IMPLEMENTATION=$(RMW_IMPLEMENTATION) ros2 launch $(BRIDGE_PKG) isaac_sim_bridge.launch.py

bridge-clean:
	rm -rf $(BRIDGE_DIR)/build $(BRIDGE_DIR)/install $(BRIDGE_DIR)/log

clean: bridge-clean
	rm -rf bin/
	rm -rf $(ZENOH_C_DIR)
	go clean
