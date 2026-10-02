# NeuroPet 2D

Lifelike Windows cockroach / fruit fly desktop pets. Six-legged gait, antenna probing, grooming, foraging and reactions to your mouse — the original 2D motion is preserved, focused on a light desktop experience.

**[English] | [中文](README.md)**

**[Download the Windows portable build](https://github.com/lexingtonhibiki/NeuroPet-2D/releases/latest)** · [Measured resources](docs/performance.md) · [Release notes](docs/release-notes-v0.2.1.md)

![Control panel](assets/demos/panel.png)

[Ten pets in continuous motion](assets/demos/ten-pets.gif) (program-generated frames, fixed 60 Hz simulation, recorded at 15 fps; no desktop capture).

The screenshot above and the GIF show the Chinese interface. Switch the language in **Settings → Language** to see the same screens in English.

## Use

1. Download the release ZIP, extract it into a writable folder, and double-click `NeuroPet-2D.exe`. Python is not required.
2. One cockroach and one fruit fly start by default. Use **Add cockroach / Add fruit fly** to keep up to **10 pets** on the desk at once. The 3-pet cap from older configurations upgrades automatically.
3. Select a pet in the list to **Feed**, **Pause** or **Hide** it. Hiding keeps its memories; **Recall** brings it back, with a notice when the desk is full.
4. Drag an insect to move it. **Pause all / Resume all** controls the whole desk; scrolling the list never changes the selected pet.
5. Closing the panel leaves the pets running. Right-click the system tray icon to reopen the panel, drop food or save and quit; without the tray, recover the panel from the taskbar.

`Ctrl+1 / Ctrl+2` add the two species, `Space` pauses the selected pet in the list, and `Esc` collapses the panel. **Settings** adjusts a pet's display size, the global crawling speed, speed trails, start with Windows, startup behaviour and the interface language; **Memories** opens on demand. Removing a pet only ends the current session and keeps its archive; clearing memories asks for confirmation.

Every setting is **applied and saved the moment you change it** — there is no save button. Language, pet size, crawling speed, trails, start with Windows and "open the panel at startup" are all independent, and the bottom of the window says so. Settings also has a **current pet** dropdown: pick a pet there to change *its* size. Without an explicit selection the window attaches to the first visible pet, hidden pets are listed too (marked, with a **Recall it** button), so you never have to hunt for the main panel first. "Save and quit" is gone — the button is **Close settings**, and Esc, the window X and that button only close the settings window while the app and its pets keep running. To quit, use **Quit** in the system tray menu.

Dropdowns stay clickable while feeding mode is on: everything belonging to the app, including an opened dropdown list, counts as click-through area, so the global mouse hook no longer treats clicking a dropdown item as a click on the desktop.

## Crawling speed and mouse reactions

- **Global crawling speed**: choose **0.5× / 1× / 2× / 3× / 4× / 6× / 8×** in Settings; the default is **2×**, noticeably faster than before. It applies immediately, is saved and restored on restart. It scales cruising speed, the escape ceiling and acceleration, and combines with the temporary speed boost some foods give; **only motion parameters change, never the global tick**, so hunger, memories, food and flight keep their own pace. Switching a step keeps each pet's position, heading and gait phase — it just accelerates or eases off, never jumps.
- **Approaching mouse = wind pressure**: the faster the cursor rushes straight at a pet, the stronger the wind and the faster it flees (up to about 1.6× at 1×). A still or receding cursor produces no wind at all; a near miss leaves only a faint draught. The reaction keeps its documented "freeze, turn, sprint, then slow down and stay alert" rhythm, and the fruit fly's takeoff response is untouched.
- **Speed trails**: past a certain speed a thin line appears behind a pet, growing with speed (capped near 240 pixels, at most 4 segments per pet) and retracting within about 0.15 seconds after it stops. On by default, switchable in Settings.

## Language

The panel, settings, memories window and the system tray menu all ship in **简体中文 / English**. Switch under **Settings → Language** — the change applies immediately and is saved, with no restart. Without a stored language the app follows the system: Chinese systems get Chinese, everything else gets English, and detection failures fall back to Chinese.

Switching language never interrupts what you were doing: the selected pet, its number, the chosen food, paused and hidden states, and any window you already opened stay exactly as they were. Pet names you typed yourself are never translated.

**Memories stay in their original wording.** What a pet wrote down is archive content: it is not machine-translated and never rewritten, so older archives keep their original language even in the English interface.

Windows 10/11 on the primary display are supported. The transparent background lets clicks reach the desktop while the insects stay draggable. Click-to-feed on the desktop ends automatically after 15 seconds; feeding straight from the panel does not need that mode.

## Screen edges and start with Windows

- Pets stay inside the **usable desktop**: the app reads the real work area of the display Windows reports, so a taskbar at the bottom, top, left or right all work and no taskbar height is hard-coded. They slow down before reaching an edge, slide along it, and never teleport; when the taskbar auto-hides or the work area changes, a pet walks back into the usable area at its normal speed.
- **Start with Windows** is a separate checkbox in Settings, off by default. It only writes `HKEY_CURRENT_USER\...\Run` — no administrator rights — and only touches this program's own value. Once ticked, Windows starts the app at sign-in straight into the system tray (no control panel); double-clicking the EXE by hand still follows the separate "open the panel at startup" switch. **Re-tick it after moving the portable folder**, otherwise the box shows unticked because the registry still points at the old path.

## Saves

`data/` next to the EXE holds the configuration, the pet roster, hidden state and each pet's memories. The configuration keeps the crawling-speed step in `crawl_speed` and the trail switch in `trail_enabled`. To update: close the old program, keep this folder, and replace the old EXE with the new one. Set `NEUROPET_DATA_DIR` to store saves somewhere else.

Start-with-Windows is deliberately **not** kept in `data/`: the registry is the only source of truth, and the app reads it every time Settings opens, so a failed write can never look like a successful registration.

The public source and release packages include the gait tables, and exclude private saves, logs and Git history from the research directory. The original NeuroPet research directory is untouched.

## Development / packaging

Windows and Python 3.13 (with tkinter):

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m neuropet.main
.venv\Scripts\python.exe tools\run_all_tests.py
.venv\Scripts\python.exe tools\build_release.py
```

`start.bat` uses the local virtual environment or Python on PATH; `build_exe.bat` produces the portable folder `dist/NeuroPet-2D/`. Move the EXE together with `_internal/` and double-click it; this packaging model needs a single resident process. Runtime dependencies are Pillow / pystray — no browser UI and no 3D drivers.

Interface text lives in one built-in dictionary, `neuropet/core/i18n.py` (zh-CN / en, no new third-party dependency); add a key with both languages to extend it. The chosen language is stored in `data/config.json` as `language` and falls back to system detection when absent.

The v0.2.0 usable-desktop logic is in `neuropet/core/desktop.py` (Win32 `MonitorFromPoint` + the `rcWork` from `GetMonitorInfo`, falling back to the whole screen off Windows); the locomotion wall, the drag arbiter and panel food drops all read it. Speed trails live in `neuropet/render/trail.py` (vector canvas lines, no bitmaps) and start-with-Windows in `neuropet/core/autostart.py` (stdlib `winreg`, no new dependency).

The measurement tools use throwaway saves and never touch your data:

```powershell
.\NeuroPet-2D.exe --probe --pets 10 --seconds 60 --output result.json
python tools\release_probe.py --pets 2 --seconds 60 --output result.json
```

Simulation updates and pose redraws are separate; movement never waits for a pose drawing quota. The image cache evicts by bytes, overlapping pets are composited once per frame, and the transparent surface is sized to real content. **60 MiB is the two-pet optimization target, not a measured result — see the performance report, and note that ten pets are counted separately.** 中文说明见 [README.md](README.md); the performance report is bilingual in one file: [docs/performance.md](docs/performance.md)。

MIT. Dependency licenses and source details are in `licenses/` inside the portable package. 3D joint reconstruction is left for future research.
