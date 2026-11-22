"""Message-key, fallback and interpolation contracts for future locale catalogs."""
from pathlib import Path
import re
import unittest

from lupa.lua54 import LuaError, LuaRuntime


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "src/PaintManager/Scripts"


class I18nTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().scripts_path = SCRIPTS.as_posix()
        self.lua.execute('package.path=scripts_path.."/?.lua;"..package.path; messages=require("paintmanager.i18n")')

    def test_default_is_english_without_any_game_api(self):
        self.assertEqual(self.lua.eval("messages.language"), "en")
        self.assertEqual(self.lua.eval('messages.text("main.queue.queued")'), "Queued")

    def test_follow_game_and_override_resolve_independently(self):
        self.lua.execute('''
            local languages=require("paintmanager.languages")
            assert(not languages.valid(nil) and languages.resolve(nil,"ru-RU")=="ru")
            assert(languages.resolve("auto","RU_ru")=="ru" and languages.resolve("auto","de-DE")=="de")
            assert(languages.resolve("ru",nil)=="ru" and languages.resolve("en","ru")=="en")
            assert(messages.select("auto","ru-RU"))
            local revision=messages.revision
            assert(messages.text("ui.settings.language_name")==messages.language_name("ru"))
            assert(not messages.select("ru","en") and messages.revision==revision)
            assert(messages.select("en","ru") and messages.text("ui.settings.language_name")=="English")
        ''')

    def test_installed_game_tags_and_platform_variants_follow_the_expected_catalog(self):
        resolve = self.lua.eval('require("paintmanager.languages").resolve')
        for game_tag, code in {
            "es": "es-ES", "es_ES": "es-ES", "ES_419": "es-419", "es-MX": "es-419",
            "es-Latn-AR": "es-419", "pt": "pt-BR", "pt_BR": "pt-BR", "fr-CA": "fr",
            "sv": "sv-FI", "sv_FI": "sv-FI", "fi-FI": "fi", "uk-UA": "uk", "it-IT": "it",
            "pl-PL": "pl", "ja-JP": "ja", "zh": "zh-Hans", "ZH_cn": "zh-Hans",
            "zh-Hans-CN": "zh-Hans", "zh-Hant-TW": "en", "zh-TW": "en", "ko-KR": "en",
        }.items():
            with self.subTest(game_tag=game_tag):
                self.assertEqual(resolve("auto", game_tag), code)
                self.assertEqual(resolve("ru", game_tag), "ru")

    def test_language_picker_names_only_load_the_selected_message_catalog(self):
        self.lua.execute('''
            local languages=require("paintmanager.languages")
            for _,code in ipairs(languages.choices) do
                if code~="auto" then
                    assert(#messages.language_name(code)>0)
                    assert(not package.loaded["paintmanager.locales."..code])
                end
            end
            messages.select("auto","zh-CN")
            assert(package.loaded["paintmanager.locales.zh-Hans"])
            assert(not package.loaded["paintmanager.locales.ja"])
        ''')

    def test_english_and_russian_integer_plural_rules_with_english_fallback(self):
        self.lua.execute('''
            local key="main.transfer.target_reached"
            local variants={one="one {count}",few="few {count}",many="many {count}"}
            local translated=messages.new({[key]=variants},"ru")
            for number,form in pairs({[0]="many","one","few","few","few","many",[11]="many",[14]="many",[21]="one",[22]="few",[111]="many"}) do
                assert(translated.text(key,{count=number})==form.." "..number)
            end
            assert(messages.new({},"ru").text(key,{count=1}):find("1 can or",1,true))
            assert(messages.new({},"ru").text(key,{count=21}):find("21 cans or",1,true))
        ''')

    def test_partial_catalog_falls_back_to_english_per_key(self):
        self.lua.execute('selected=messages.new({["main.queue.queued"]="Scheduled"})')
        self.assertEqual(self.lua.eval('selected.text("main.queue.queued")'), "Scheduled")
        self.assertEqual(self.lua.eval('selected.text("main.status.closed")'), "Window closed")
        self.assertEqual(self.lua.eval('messages.text("main.queue.queued")'), "Queued")

    def test_named_placeholders_can_be_reordered_in_a_future_translation(self):
        result = self.lua.execute('''
            local selected=messages.new({["main.progress.total"]="{completed} of {total}: {phase}"})
            return selected.text("main.progress.total",{completed=2,total=9,phase="Scanning"})
        ''')
        self.assertEqual(result, "2 of 9: Scanning")

    def test_values_are_literal_unicode_and_not_recursive_templates(self):
        self.lua.globals().native_name = "Paint 100% {not_a_parameter} 🎨 \u6f06"
        result = self.lua.eval('messages.text("main.progress.count",{phase=native_name,completed=7})')
        self.assertEqual(result, "Paint 100% {not_a_parameter} 🎨 \u6f06 · 7")

    def test_missing_required_parameters_fail_clearly(self):
        with self.assertRaisesRegex(LuaError, "Missing or invalid message parameter"):
            self.lua.execute('return messages.text("main.progress.total",{phase="Scanning",completed=1})')

    def test_unknown_keys_are_developer_errors(self):
        with self.assertRaisesRegex(LuaError, "Unknown English message key"):
            self.lua.execute('return messages.text("missing.message")')
        with self.assertRaisesRegex(LuaError, "Unknown translation key"):
            self.lua.execute('return messages.new({["missing.message"]="Unexpected"})')

    def test_translation_placeholder_mismatch_is_rejected(self):
        for template in ("{phase}", "{phase} {completed} {extra}"):
            with self.subTest(template=template), self.assertRaises(LuaError):
                self.lua.globals().template = template
                self.lua.execute('return messages.new({["main.progress.count"]=template})')

    def test_catalogs_and_overlays_cannot_mutate_the_default(self):
        self.lua.execute('''
            local copy=messages.english_catalog();copy["main.queue.queued"]="Changed"
            local overlay={["main.queue.queued"]="Scheduled"}
            selected=messages.new(overlay);overlay["main.queue.queued"]="Changed"
        ''')
        self.assertEqual(self.lua.eval('messages.text("main.queue.queued")'), "Queued")
        self.assertEqual(self.lua.eval('selected.text("main.queue.queued")'), "Scheduled")

    def test_static_ui_and_coordinator_message_keys_exist(self):
        catalog = dict(self.lua.eval("messages.english_catalog()"))
        for source in (SCRIPTS / "main.lua", SCRIPTS / "paintmanager/ui.lua"):
            keys = set(re.findall(r'\bt\("([a-z0-9_.]+)"', source.read_text(encoding="utf-8")))
            self.assertTrue(keys, source)
            self.assertFalse(keys - catalog.keys(), (source, keys - catalog.keys()))

    def test_authored_project_files_have_no_literal_cyrillic_outside_future_locales(self):
        paths = [ROOT / name for name in ("README.md", "AGENTS.md", "CHANGELOG.md")]
        for directory in ("src", "tests", "tools", "docs"):
            paths.extend(path for path in (ROOT / directory).rglob("*")
                         if path.suffix in {".lua", ".py", ".md", ".json", ".txt"}
                         and not (path.parent == SCRIPTS / "paintmanager/locales"
                                  and not path.name.startswith("en_")))
        forbidden = re.compile(r"[\u0400-\u052f]")
        offenders = [str(path.relative_to(ROOT)) for path in paths
                     if forbidden.search(path.read_text(encoding="utf-8-sig"))]
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
