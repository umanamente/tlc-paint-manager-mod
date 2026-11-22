"""Case-insensitive search in the languages shipped by the game."""
from pathlib import Path
import unittest

from lupa.lua54 import LuaRuntime


SCRIPTS = Path(__file__).resolve().parents[1] / "src/PaintManager/Scripts"


class DisplayTextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lua = LuaRuntime(unpack_returned_tuples=True)
        cls.lua.globals().scripts = SCRIPTS.as_posix()
        cls.lua.execute('package.path=scripts.."/?.lua;"..package.path')
        cls.fold = cls.lua.eval('require("paintmanager.display_text").fold')

    def test_supported_latin_and_cyrillic_letters(self):
        for word in ("ÉCŒURÉ Ÿ", "GRÜN WEIß WEIẞ", "CARMESÍ CAÑÓN", "AZUL-PETRÓLEO",
                     "CITTÀ PIÙ", "ZAŻÓŁĆ GĘŚLĄ JAŹŃ", "BLÅ GRÖN", "ŠAKKI Ž",
                     "\u0490\u0404\u0406\u0407"):
            with self.subTest(word=word):
                self.assertEqual(self.fold(word), word.casefold())

    def test_unmapped_characters_and_combining_marks_survive(self):
        self.assertEqual(self.fold("A\u0301 × 青 ア 🟣 Θ"), "a\u0301 × 青 ア 🟣 Θ")

    def test_german_sharp_s_matches_ascii_query_in_paint_search(self):
        self.assertTrue(self.lua.execute('''
            local view = require("paintmanager.paint_view")
            return view.matches({status="captured"}, {displayName="Weiß"}, {query="WEISS"})
        '''))
