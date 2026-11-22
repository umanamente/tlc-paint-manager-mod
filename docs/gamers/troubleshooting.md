# Troubleshooting

| Symptom | What to check |
| --- | --- |
| The window does not open | Leave vehicle controls, close the current native menu, and press **Ctrl+Alt+F10**. Try **Open PaintManager** in the cockpit paint editor. Verify the final mod folder and look for PaintManager startup entries in `ue4ss/UE4SS.log`. |
| The window shows **Signal lost** | Move closer to the selected ship. The default connection radius is 35 m. Eligible boxes also use distance from the ship. |
| I can see a color but have no cans | Learned recipes and physical cans are counted separately. Check your backpack and in-range player storage boxes. The game must have loaded the box. |
| A quantity is unknown or lower than expected | Wait for the current scan, then close and reopen the window near the ship. Incomplete readings can show an unknown quantity or a known minimum. |
| **Learn** is unavailable | Look for an unlearned recipe on **Can Learn**. You need a complete stock read with at least four matching cans, current recipe information and the reference game build. |
| A gather or return skipped paint | Check free slots and weight in the destination. For a return, find the original box and leave it loaded. A manually rearranged stack may have lost its identifiable origin. |
| A preset cannot be applied | Check the selected target, available paint and space in the backpack for every removal. Read the reported outcome before deciding which zone still needs work. |
| The game mirror is pending | Reopen the window or refresh presets after the save finishes loading. Once staging completes, make an ordinary game save. |
| Local saving is unavailable | The current file adapter supports ASCII Windows paths. If the path to your save directory contains other characters, library publication can fail. Keep the original files when reporting the issue. |

For a bug report, include the PaintManager version from `mod.json`, the Steam game build, your UE4SS version, the steps that reproduce the issue and what the window reported. A short excerpt of the PaintManager lines from `ue4ss/UE4SS.log` is usually enough. Remove personal paths and account identifiers before sharing.

[Installation](installation.md) · [Player guide](guide.md)
