# Installation, updates and removal

## Requirements

PaintManager targets the Windows Steam version of **The Last Caretaker**. Its reference game build is **25982825**. Learning, inventory transfers and applying presets require that build. The project uses UE4SS **3.0.1 Beta #0**, revision `527a483b`, with the loader's Lua runtime enabled. Install a compatible loader separately. [UE4SS project](https://github.com/UE4SS-RE/RE-UE4SS) · [Loader installation guide](https://docs.ue4ss.com/installation-guide.html)

## Install the release ZIP

1. Close the game. In Steam, use **Manage → Browse local files** for The Last Caretaker. Open `Voyage/Binaries/Win64/`. Your UE4SS installation should already have an `ue4ss/Mods/` directory there.
2. Download the release asset named `PaintManager-<version>.zip`. GitHub's automatically generated **Source code** downloads contain the development project and have a different layout.
3. Extract the contents of the release ZIP into `Win64/`, preserving its directory structure. Confirm that the resulting file is `ue4ss/Mods/PaintManager/Scripts/main.lua` and that `enabled.txt` is alongside the `Scripts` folder.
4. Start the game and load a save. Near your ship, close any native menus and press **Ctrl+Alt+F10** from gameplay. **Open PaintManager** in the cockpit paint editor also opens the window.

## Update or remove

For a manual installation, close the game, move your existing `ue4ss/Mods/PaintManager/` folder to a backup location outside `Mods`, then extract the new ZIP into `Win64/`. To uninstall, move the PaintManager folder out of `Mods`. If a mod manager installed the mod, use that manager for updates and removal.

The mod folder can contain diagnostics you may want to keep. Your account's `PaintManager.library.json` lives in the game's save directory, separately from the mod folder. Ordinary game saves can also contain presets, preferences and already learned recipes. Removing the mod leaves these records in place.

[Using PaintManager](guide.md) · [Troubleshooting](troubleshooting.md)
