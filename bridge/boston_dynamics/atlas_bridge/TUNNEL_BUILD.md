# Building the Go tunnel for the end-to-end demo

`demo_go_tunnel.py` drives the repository's own Go tunnel — the binary that
mounts the upstream x402 gin middleware and a real facilitator client — so the
payment decision is made by the tunnel rather than by any Python code.

The tunnel is not part of this profile; these are the steps that produced the
binary used for the committed evidence, recorded so a reviewer can repeat them.

## What the tunnel needs

`tunnel/` imports `github.com/eclipse-zenoh/zenoh-go`, which is a cgo binding.
Building it therefore needs three things beyond the Go toolchain:

| | |
| --- | --- |
| Go | 1.25+ |
| zenoh-c | matching native library, headers and import library |
| C toolchain | a **complete** one — see the note below |

## Linux / macOS

```bash
curl -LO https://github.com/eclipse-zenoh/zenoh-c/releases/download/1.9.0/zenoh-c-1.9.0-x86_64-unknown-linux-gnu-standalone.zip
unzip zenoh-c-1.9.0-x86_64-unknown-linux-gnu-standalone.zip -d zenohc
```

```bash
CGO_ENABLED=1 \
CGO_CFLAGS="-I$PWD/zenohc/include" \
CGO_LDFLAGS="-L$PWD/zenohc/lib -lzenohc" \
go build -o tunnel ./tunnel/cmd
```

Put `zenohc/lib` on `LD_LIBRARY_PATH` (or `DYLD_LIBRARY_PATH`) when running it.

## Windows

```bash
curl -LO https://github.com/eclipse-zenoh/zenoh-c/releases/download/1.9.0/zenoh-c-1.9.0-x86_64-pc-windows-gnu-standalone.zip
```

Extract it, then build with a MinGW-w64 toolchain:

```bash
CGO_ENABLED=1 \
CC=/path/to/mingw64/bin/gcc.exe \
CGO_CFLAGS="-I/path/to/zenohc/include" \
CGO_LDFLAGS="-L/path/to/zenohc/lib -lzenohc" \
go build -o tunnel.exe ./tunnel/cmd
```

Copy `zenohc/bin/zenohc.dll` next to `tunnel.exe` before running it.

**Two Windows traps worth knowing**, both of which cost real time here:

* Go's linker invokes whatever `CC` resolves to, **not** the first `gcc` on
  `PATH`. If an older broken toolchain is installed, the build fails with
  `cannot execute '…/collect2.exe'` even though a working `gcc` is earlier on
  the path. Set `CC` explicitly.
* Some MinGW distributions ship `gcc.exe` without the matching
  `libexec/gcc/**/collect2.exe`, so they can compile but not link. Verify with
  `find <toolchain> -name collect2.exe` before assuming the toolchain is fine.

## Running the demo

```bash
python -m bridge.boston_dynamics.atlas_bridge.demo_go_tunnel --tunnel /path/to/tunnel
```

The demo starts the Atlas bridge, stands up a minimal WebSocket proxy in place
of the hosted Fabric backend, launches the tunnel against it, and sends an
unpaid action followed by a forged payment. It exits non-zero unless the tunnel
refuses both and the simulator is never reached.

Tunnel configuration is read from `config.json` beside the binary:

```json
{
  "robot_id": "atlas-sim-01",
  "evm_payee_address": "0x<your payee>",
  "price": "$0.001",
  "network": "eip155:84532"
}
```

## What this proves, and what it does not

**Proves** — with the real tunnel and the real x402 middleware in the path:

* an unpaid action is refused with `402` and payment requirements are advertised;
* a structurally valid but unsigned authorization is refused after the
  middleware consults the live facilitator;
* neither request ever reaches Zenoh or the simulator.

**Does not prove** — the accepting side. A payment the facilitator will accept
needs an EIP-3009 authorization signed by a funded wallet. No key material
belongs in this repository, so that path is exercised at deployment time with
the operator's own wallet and is not claimed here.
