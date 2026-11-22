# Using PaintManager

## Find a paint or learn a recipe

Open the window with **Ctrl+Alt+F10** while on foot, or choose **Open PaintManager** in the cockpit paint editor. Press **Escape** to close it. PaintManager reads the backpack and loaded Small, Medium and Large player storage boxes within range of the selected ship. The default radius is **35 m** and can be raised to **50 m** in **Settings**.

Use **Learned** for recipes your character already knows, **Can Learn** for recipes ready to learn and **Need Cans** to see which unlearned colors need more stock. Search by the name shown in the game. A learned recipe can have zero cans.

Learning requires four matching physical cans across eligible storage and the backpack. Wait for the stock count to finish, select the color and click **Learn**. The mod checks your current stock and preserves the cans. **Learn all** processes all eligible colors, including results hidden by the current search. Opening the window alone only reads the collection.

## Gather, return and deposit cans

- **Top up** brings the selected color from ship boxes into your backpack. The target counts cans you already carry. With a target of five and two in your backpack, the mod looks for up to three more. In **Settings**, choose one, five, a custom number or all available cans of that color.
- **Move all paints to backpack** handles every color, including duplicates, until a slot or weight limit prevents further transfers.
- **Return to original box** sends a borrowed can back. **Return all paints to boxes** also works on the **Signal lost** screen. Each original box must still be loaded and accessible with room for the return. Moving or mixing stacks yourself can make their origins impossible to verify. The result reports confirmed moves and skips.
- In an open player box that already contains paint, **Group all colors in Deposit similar** lets the game's deposit action accept every paint color from your backpack. The setting defaults on. **Show Deposit all paints button** is a separate option, off by default. Enable it to show a paint can beside **Deposit all paints** when both inventories contain paint.

## Save and apply a paint scheme

1. Open **Presets** and choose **Ship** or **Character**. Save the current configuration and give it a name. The native ship paint editor and character paint bar also have a **Save Preset** button.
2. Select a saved row to see the paint for each zone. **Current** identifies a layout matching the selected target.
3. Before clicking **Apply**, leave backpack space for removed cans. PaintManager uses backpack paint first and looks for missing colors in eligible ship boxes. A color swap returns the old can to your backpack before equipping the replacement. If the replacement cannot be completed, read the result and inspect the zone and backpack.

Your account library stores presets and preferences locally. An ordinary game save persists the staged copy for the loaded save. Use the game's usual save action when you want to keep the session's changes.

## Language

PaintManager starts with **Follow game language**. To use a different language for the mod, open **Settings → PaintManager language** and select the language by its own name. The choice is saved with your preferences. Choose **Follow game language** to switch back. Paint names follow the game's language, and your saved paint schemes keep the names you gave them.

PaintManager supports all 12 interface languages listed on Steam. These are English, French, German, Italian, Japanese, Polish, Brazilian Portuguese, Russian, Simplified Chinese, Spanish (Spain), Spanish (Latin America) and Ukrainian. Both Spanish translations have their own buttons, **Español (España)** and **Español (Latinoamérica)**. [The Last Caretaker on Steam](https://store.steampowered.com/app/1783560/The_Last_Caretaker/?l=english)

PaintManager also offers Finnish (**Suomi**) and Swedish as used in Finland (**Svenska (Finland)**). The reference game build includes localization resources for both. If the game language has no matching translation, PaintManager uses English. Developer tools and diagnostic output can appear in English.

[Troubleshooting](troubleshooting.md)
