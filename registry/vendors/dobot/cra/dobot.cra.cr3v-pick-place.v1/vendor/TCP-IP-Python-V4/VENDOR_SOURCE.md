# Official DOBOT SDK source record

Upstream repository: <https://github.com/Dobot-Arm/TCP-IP-Python-V4>

Pinned field-integration commit:
`d651981db004f2c906625b2eba007e5f873a6151` (2026-07-13). The repository's
default branch changed after the field run, so deployments must not rely on an
unpinned clone.

The field integration used DOBOT's official `dobot_api.py`. The implementation
is not duplicated in this small robot profile; obtain it from the official
repository and pass its directory with `--sdk-dir`.

The files inspected for the physical integration were recorded on 2026-07-14:

| Upstream file | Git blob SHA |
| --- | --- |
| `dobot_api.py` | `90f430da8787cb72246920c1c3b8e6651367fcfb` |
| `README-EN.md` | `5cb4321ad9aab194d359297c676947a199ab4fd6` |
| `LICENSE` | `b25b673802474d326e533ada9c37203a38c3bb52` |

License: MIT. The upstream license text is preserved verbatim in `LICENSE`.

The bridge uses the SDK's `DobotApiDashboard` methods `RunScript`,
`RobotMode`, and `Stop`. The SDK version inspected does not expose a
`GetScrName` convenience method, so the bridge sends the fixed read-only
`GetScrName()` command through the SDK transport and strictly parses its reply.
No request value is interpolated into that command.

Reproduce the exact SDK source:

```bash
git clone https://github.com/Dobot-Arm/TCP-IP-Python-V4.git vendor-sdk
git -C vendor-sdk checkout d651981db004f2c906625b2eba007e5f873a6151
git -C vendor-sdk hash-object dobot_api.py
# expected: 90f430da8787cb72246920c1c3b8e6651367fcfb
```
