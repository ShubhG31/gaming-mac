# gaming-mac

Performance-tuned, security-hardened fork of [`fxgl/steamac`](https://github.com/fxgl/steamac) (based on `v1.9.15`, `79aeadb`) for running ARM64 SteamOS, Steam, and Epic Games on Apple Silicon Macs.

> **Attribution & Licensing:** This repository is a fork of [fxgl/steamac](https://github.com/fxgl/steamac). All original license and notice files ([`LICENSE`](LICENSE), [`NOTICE`](NOTICE), and [`LICENSES/`](LICENSES/)) are preserved unchanged: project files remain Apache 2.0, Linux kernel patches/config remain GPL-2.0, and third-party driver patches follow their upstream licenses.

---

## What Changed vs. Upstream `fxgl/steamac`

### 1. Full-Stack Apple Silicon Performance Optimizations

| Layer | Key Changes |
|---|---|
| **DXMT / `wmtr` Transport** (`wmtr/`) | Bumped wire format to `WMTR_VERSION 3` with 128-byte Apple Silicon cache-line alignment for `wmtr_ring` and `wmtr_slot` atomics; lowered default client spin (`WMTR_SPIN_US=20`) and host spin (`WMTR_HOST_SPIN_US=150`) to eliminate vCPU/host burn; hoisted host-side relocations and zeroing; lowered `wmtr.compile` GCD QoS to `USER_INITIATED`; defaulted `CAMetalLayer.maximumDrawableCount` to `3` (`WMTR_DRAWABLES=2` opts into double-buffering); skipped capture hooks when inactive. |
| **macOS Launcher & Display** (`host/launcher/`) | Replaced single-texture scanout with a 3-slot `.shared` `MTLTexture` ring; skipped host Metal composition while the window is occluded/hidden; defaulted refresh rate to the display's native rate (up to 120 Hz on ProMotion); tuned default VM sizing and `shmMiB` headroom (`max(8192, gpuBudgetMiB + 2048)`); tightened frame pacing stats and stall diagnostics; enabled LTO/release compiler flags in `build.sh`. |
| **Guest Agent, Audio & Layer** (`guest/`) | Fixed `progress-agent` cursor polling to sleep at 0 Hz when hidden instead of waking continuously; added PipeWire quantum tuning (`99-steamac-quantum.conf`) for crackle-free low-latency audio under VM jitter; tuned `gamescope-session`, `steam.service` drop-in (`50-steamac.conf`), `steam-dxmt-arm64ec`, and `steam-gfx-path`; optimized Mesa aarch64 container build flags. |
| **Guest Kernel** (`guest/kernel/`) | Refined Apple IMPDEF TSO (`ACTLR_EL1`) patches (`0004`, `0005`) and tuned `20-steamac.config` for lower VM scheduling and I/O overhead while keeping CPU security mitigations enabled. |
| **Host Drivers & VMM** (`host/{kosmickrisp,virglrenderer,libkrun,dxmt}/`) | Enabled Apple Silicon release optimization flags (`-O3`, LTO where supported); trimmed hot-path logging/lock contention across KosmicKrisp (`0047`–`0100`), virglrenderer (`0008`, `0010`), libkrun (`0007`, `0008`, `0016`, `0018`), and DXMT residency-set patch (`0023`). |

### 2. Security Hardening

- **Narrowed `FEX_*` Profile Allowlist (`host/launcher/Sources/steamac-vm/GameProfiles.swift`):** Replaced any broad `FEX_*` matching with an explicit allowlist of performance-tuning keys only (`FEX_X87REDUCEDPRECISION`, `FEX_MULTIBLOCK`, `FEX_HALFBARRIERTSOENABLED`, `FEX_VOLATILEMETADATA`, `FEX_TSOENABLED`, `FEX_VECTORTSOENABLED`), rejecting dangerous keys such as `FEX_ROOTFS` or `FEX_OUTPUTLOG` (verified in `GameProfile.selfCheck()`).
- **Guest Kernel Mitigations Preserved (`host/launcher/Sources/steamac-vm/Options.swift`):** Keeps standard ARM64 CPU vulnerability mitigations active by default (only `audit=0` is added to the guest kernel cmdline, never `mitigations=off`).
- **`wmtr` Executor Hardening (`wmtr/src/wmtr_host.m`):**
  - Eliminated double-fetch header TOCTOU by pinning the validated `struct wmtr_req` header into private host `scratch`/`copy` memory after copying from the guest-shared ring.
  - Added `NULL` checks on `malloc`/`realloc` allocations in `executor()` so OOM cleanly stops the ring instead of dereferencing `NULL`.
  - Preserved full `WMTR_COPY_SLACK` zeroing whenever `n_reloc != 0` and a 256-byte zeroed guard tail otherwise.
  - **Trust-Boundary Note:** `wmtr` is a high-throughput local Metal remoting transport designed for a single-user trusted guest VM; it is not intended as a multi-tenant untrusted-guest security boundary.

---

## Epic Games & Non-Steam Support (`steamac-epic`)

`gaming-mac` ships a first-class ARM64 Epic Games integration powered by the native ARM64 Flatpak of [Heroic Games Launcher](https://heroicgameslauncher.com/) (`com.heroicgameslauncher.hgl`), wired into SteamOS's Proton, FEX, DXMT, and Venus/KosmicKrisp graphics environment.

### Quick Start (Inside SteamOS)

1. Open **Desktop Mode** (or SSH into the VM) and run:
   ```sh
   steamac-epic setup          # Installs Heroic via Flathub, applies minimal Flatpak overrides,
                               # and seeds Proton/FEX/wrapper defaults without clobbering existing config
   ```
   To default Epic titles to DXMT Metal remoting when available:
   ```sh
   steamac-epic setup --dxmt
   ```
2. Launch Heroic from the **Epic Games (Heroic)** application menu icon or via:
   ```sh
   steamac-epic launch
   ```
3. Sign in to Epic Games inside Heroic and install games normally. Because `addToSteam` is enabled by default, installed Epic titles automatically appear as Non-Steam shortcuts in **Steam Gaming Mode** and launch through `steamac-epic run-game --`, which propagates:
   - Active Venus / MoltenVK / KosmicKrisp environment from `$XDG_RUNTIME_DIR/steam/env/gfx.txt` (falling back to `~/.config/steam/env/gfx.txt`).
   - Active `DXMT_CONFIG` (merging any `steamac.dxmt_config=` kernel cmdline overrides).
   - Safe FEX & Proton defaults (`FEX_X87REDUCEDPRECISION=1`, `PROTON_USE_NTSYNC=1`).
4. Inspect configuration and detected Proton/DXMT tools at any time:
   ```sh
   steamac-epic status
   ```

### Anti-Cheat Compatibility Note

Single-player games, server-authoritative multiplayer titles, and games using official Linux/Proton userspace anti-cheat runtimes work through Proton/FEX. Games that require Windows kernel-mode anti-cheat drivers (e.g., Vanguard, kernel-mode EAC/BattlEye without Proton support) cannot run inside a Linux/Proton VM.

---

## Building & Testing

### Disk Space & External SSD Builds

A full `./build.sh` (MoltenVK, KosmicKrisp, virglrenderer, libkrun, launcher, Linux kernel, Mesa, and SteamOS disk image) requires **~150 GB of free space** plus Docker/OrbStack storage. If your internal Mac SSD is tight on space, clone and build directly on a fast external APFS SSD (and point Docker/OrbStack disk storage there as well):

```sh
cd /Volumes/YourExternalSSD
git clone https://github.com/ShubhG31/gaming-mac.git
cd gaming-mac
./build.sh
./build.sh dxmt
./run.sh
```

See [`README.md`](README.md) for the complete upstream build matrix, requirements, and runtime flags.

### Local Verification Suite (No Full Build Required)

```sh
# Guest scripts, Epic Games CLI, audio, affinity, and graphics env unit tests
python3 scripts/test/steamac-epic.py
python3 scripts/test/vcpu-affinity-config.py
python3 scripts/test/steam-dxmt-arm64ec.py
python3 scripts/test/steam-compat-default.py
python3 scripts/test/steam-title-defaults.py
python3 scripts/test/audio-layout.py
bash scripts/test/steam-gfx-env.sh
bash scripts/test/steam-gfx-path.sh

# wmtr spin & kick-coalescing unit test (with UBSan)
clang -std=c11 -O2 -g -Wall -Wextra -Werror -fsanitize=undefined \
  -Iwmtr/include wmtr/tests/wmtr_spin_test.c -o /tmp/wmtr_spin_test -lpthread \
  && /tmp/wmtr_spin_test && rm -f /tmp/wmtr_spin_test

# Swift launcher syntax check
swiftc -parse host/launcher/Sources/steamac-vm/*.swift
```
