"""Cross-check the translator's PO with the messages actually selected by Lua."""
import gettext
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import polib
from lupa.lua54 import LuaRuntime

from tools import localization


ROOT = Path(__file__).resolve().parents[1]


class GettextCatalogTests(unittest.TestCase):
    def test_compiler_skips_unreviewed_text_and_rejects_a_broken_placeholder(self):
        pot, _ = localization.template(ROOT)
        po = polib.pofile(str(ROOT / "translations/ru.po"))
        lookup = {e.msgctxt: e for e in po}
        label = lookup["ui.transfer.gather_target"]
        label.flags.append("fuzzy")
        lookup["ui.button.close"].msgstr = ""
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "translations").mkdir()
            path = root / "translations/ru.po"
            po.save(str(path))
            module = localization.compile_catalog(root, "ru", pot)
            self.assertNotIn('"ui.transfer.gather_target"', module)
            self.assertNotIn('"ui.button.close"', module)
            label.flags.remove("fuzzy")
            label.msgstr = "Missing number"
            po.save(str(path))
            with self.assertRaisesRegex(ValueError, "placeholder mismatch"):
                localization.compile_catalog(root, "ru", pot)

    def test_every_russian_message_matches_gnu_gettext_in_game_runtime(self):
        localization.generate(ROOT, check=True)
        pot = polib.pofile(str(ROOT / "translations/PaintManager.pot"))
        ru = polib.pofile(str(ROOT / "translations/ru.po"))
        self.assertEqual(ru.percent_translated(), 100)
        self.assertEqual({e.msgctxt for e in pot}, {e.msgctxt for e in ru if not e.obsolete})
        self.assertTrue(all(e.comment and e.occurrences for e in pot))
        gnu = gettext.GNUTranslations(io.BytesIO(ru.to_binary()))
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().scripts = (ROOT / "src/PaintManager/Scripts").as_posix()
        lua.execute('package.path=scripts.."/?.lua;"..package.path; i=require("paintmanager.i18n"); i.select("ru")')
        render = lua.eval("i.text")
        plurals = lua.eval('require("paintmanager.locales.en_plurals")')
        for entry in ru:
            with self.subTest(key=entry.msgctxt):
                values = {arg: "{value} 10%" for arg in localization.arguments(entry.msgid)}
                if entry.msgid_plural:
                    for n in (0, 1, 2, 5, 11, 12, 14, 20, 21, 22, 51, 101, 111, 112):
                        values[plurals[entry.msgctxt]["count"]] = n
                        text = gnu.npgettext(entry.msgctxt, entry.msgid, entry.msgid_plural, n)
                        self.assertEqual(render(entry.msgctxt, lua.table_from(values)), text.format_map(values))
                else:
                    text = gnu.pgettext(entry.msgctxt, entry.msgid)
                    self.assertEqual(render(entry.msgctxt, lua.table_from(values)), text.format_map(values))
        keys = {entry.msgctxt for entry in pot}
        self.assertIn("ui.settings.developer", keys)
        self.assertFalse(any(key.startswith(("ui.debug.", "ui.processing.", "main.processing.")) for key in keys))
        self.assertNotIn("ui.tab.debug", keys)
        self.assertEqual(render("ui.tab.debug"), "Debug")

    def test_russian_search_and_suggested_names_keep_player_input(self):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().scripts = (ROOT / "src/PaintManager/Scripts").as_posix()
        lua.execute('package.path=scripts.."/?.lua;"..package.path; i=require("paintmanager.i18n")')
        # Use Unicode escapes here because source outside translation catalogs is English.
        lua.globals().upper = "\u0416\u0401\u041b\u0422\u042b\u0419"
        lua.globals().lower = "\u0436\u0451\u043b\u0442\u044b\u0439"
        lua.execute('''
            local names = require("paintmanager.display_text")
            assert(names.fold(upper) == lower)
            assert(names.sort_key(utf8.char(0x435)) < names.sort_key(utf8.char(0x451)))
            assert(names.sort_key(utf8.char(0x451)) < names.sort_key(utf8.char(0x436)))
            local paint_view = require("paintmanager.paint_view")
            assert(paint_view.matches({status="captured"}, {displayName=upper, recipeState="unlocked"}, {query=lower}))
            i.select("ru")
            local existing = i.text("ui.preset.suggest_name", {number=1})
            local record = {name=existing}
            local job = require("paintmanager.preset_index").begin({presets={record}})
            while job:step().state == "running" do end
            local index = job:get_result()
            assert(index:suggest_name() == i.text("ui.preset.suggest_name", {number=2}))
            i.select("en")
            assert(index:suggest_name() == "Preset 1")
            assert(record.name == existing)
        ''')


if __name__ == "__main__":
    unittest.main()
