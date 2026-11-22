# Testing

Run the [checkout checks](development.md#verify-a-checkout) for code and packaging changes. Python tests execute Lua against game-shaped fixtures, check persisted data and validate the release ZIP. Unreal widget lifetime, native transfer semantics and save restoration also need observations in a real game session.

## Manual release smoke test

Use a backed-up test save and the candidate release ZIP. Record the mod version, game build, UE4SS revision, language and which steps actually ran.

| Check | Expected observation |
| --- | --- |
| Install and open | The ZIP installs under `ue4ss/Mods/PaintManager/` and UE4SS logs a successful startup. Near the ship, open with Ctrl+Alt+F10, close with Escape and reopen using the cockpit editor. Verify mouse and game input return on close. |
| Load and lose signal | Read the three paint tabs, move beyond the selected radius, then return and reopen. Confirm the disconnected view clears and loaded boxes are counted again. |
| Learn | With four matching cans, note backpack and box totals. Use **Learn** on one locked recipe. Check membership in the game's own interface and compare the same inventory totals afterward. |
| Gather and return | Top up a color from two identified boxes and return the borrowed cans. Check both quantities and original destinations. Try a full destination, then cancel a bulk operation between moves. |
| Deposit | With a box that already contains paint, try **Deposit similar** with its grouping option on and off. Enable **Show Deposit all paints button**, click the paint can and verify non-paint items remain in the backpack. |
| Presets and persistence | Save one ship scheme and one character scheme. Apply a changed layout with a free backpack slot. Inspect both the equipped and removed cans. Make an ordinary game save, restart and reload, then check the presets and settings again. |
| UI and localization | Switch between English, Russian and the game language. Inspect long settings captions and help at narrow and wide sizes. Leave the window open for several minutes, switch tabs and check input and textures. |
| Remove | Close the game and remove the mod via the installation method used. Confirm that the test save can be opened and the usual game controls work. |

For a native call change, compare the actual receiver, argument types, return and observed effects with the [call inventory](native-call-contracts.json). A fixture can model a return value even when the actual game binding differs. Include the observed result or the outstanding scenario in release notes.
