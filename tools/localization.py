"""Export annotated English messages to gettext and compile reviewed PO files into Lua."""
import argparse
import gettext
import json
import re
from pathlib import Path

import polib


ROOT = Path(__file__).resolve().parents[1]
LOCALES = Path("src/PaintManager/Scripts/paintmanager/locales")
ENTRY = re.compile(r'^\s*\["([^"]+)"\]\s*=\s*("(?:[^"\\]|\\.)*"),\s*$')
PLACEHOLDER = re.compile(r"\{([a-zA-Z_]\w*)\}", re.ASCII)
# Gettext indices and their corresponding Lua forms for nonnegative integer counts.
PLURAL_RULES = {
    "invariant": (("other",), "nplurals=1; plural=0;"),
    "one_other": (("one", "other"), "nplurals=2; plural=(n != 1);"),
    "zero_one": (("one", "other"), "nplurals=2; plural=(n > 1);"),
    "east_slavic": (("one", "few", "many"), "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : "
                    "n%10>=2 && n%10<=4 && (n%100<12 || n%100>14) ? 1 : 2);"),
    "polish": (("one", "few", "many"), "nplurals=3; plural=(n==1 ? 0 : "
               "n%10>=2 && n%10<=4 && (n%100<12 || n%100>14) ? 1 : 2);"),
}
# The order is used in Settings, after automatic selection and English.
LANGUAGES = {
    "de": "one_other", "es-ES": "one_other", "es-419": "one_other", "fr": "zero_one",
    "it": "one_other", "pl": "polish", "pt-BR": "zero_one", "fi": "one_other",
    "sv-FI": "one_other", "ru": "east_slavic", "uk": "east_slavic",
    "ja": "invariant", "zh-Hans": "invariant",
}
HEADER = {"Project-Id-Version": "PaintManager", "MIME-Version": "1.0",
          "Content-Type": "text/plain; charset=UTF-8", "Content-Transfer-Encoding": "8bit"}


def arguments(message):
    names = set(PLACEHOLDER.findall(message))
    remainder = PLACEHOLDER.sub("", message)
    if "{" in remainder or "}" in remainder:
        raise ValueError(f"Invalid named placeholder in {message!r}")
    return names


def template(root):
    pot = polib.POFile(wrapwidth=0)
    pot.metadata = HEADER.copy()
    plurals = {}
    seen = set()
    for name in ("en_ui.lua", "en_main.lua"):
        path = LOCALES / name
        notes = []
        plural = None
        english_only = False
        for number, line in enumerate((root / path).read_text(encoding="utf-8").splitlines(), 1):
            value = line.strip()
            if value in {"-- BEGIN ENGLISH ONLY", "-- END ENGLISH ONLY"}:
                opening = value == "-- BEGIN ENGLISH ONLY"
                if opening == english_only or notes or plural:
                    raise ValueError(f"Invalid English-only block at {path}:{number}")
                english_only = opening
                continue
            if value.startswith("-- TRANSLATORS:"):
                notes.append(value.partition(":")[2].strip())
                continue
            if value.startswith("-- PLURAL "):
                count, singular = value.removeprefix("-- PLURAL ").split(": ", 1)
                plural = (count, json.loads(singular))
                continue
            match = ENTRY.fullmatch(line)
            if match:
                key, quoted = match.groups()
                if key in seen:
                    raise ValueError(f"Duplicate English key {key}")
                seen.add(key)
                if not english_only and not notes:
                    raise ValueError(f"Missing translator context for {key} at {path}:{number}")
            elif value.startswith('["'):
                raise ValueError(f"Invalid catalog entry at {path}:{number}")
            if not notes and not plural:
                continue
            if not match or english_only or not all(notes) or not notes:
                raise ValueError(f"Translator metadata must precede a message at {path}:{number}")
            source = json.loads(quoted)
            entry = polib.POEntry(msgctxt=key, msgid=source, comment="\n".join(notes),
                                  occurrences=[(path.as_posix(), str(number))],
                                  flags=["python-brace-format"] if arguments(source) else [])
            if plural:
                count, singular = plural
                if count not in arguments(source) or arguments(singular) != arguments(source):
                    raise ValueError(f"Invalid English plural for {key}")
                entry.msgid, entry.msgid_plural = singular, source
                entry.msgstr_plural = {0: "", 1: ""}
                plurals[key] = (count, singular)
            pot.append(entry)
            notes, plural = [], None
        if notes or plural or english_only:
            raise ValueError(f"Unattached translator metadata in {path}")
    # The note identifies the actual widget even when Lua constructs its key
    # dynamically. Add file references wherever the full key occurs in code.
    by_key = {entry.msgctxt: entry for entry in pot}
    literal_key = re.compile(r'"((?:ui|main|preset)\.[a-zA-Z0-9_.]+)"')
    scripts = (root / LOCALES).parents[1]
    for file in sorted(scripts.rglob("*.lua")):
        if file.parent == root / LOCALES:
            continue
        keys = set(literal_key.findall(file.read_text(encoding="utf-8-sig")))
        for key in keys & by_key.keys():
            by_key[key].occurrences.append((file.relative_to(root).as_posix(), ""))
    return pot, plurals


def lua_string(value):
    # Escape controls with Lua's decimal escapes. Preserve UTF-8 as literal text.
    return '"' + re.sub(r'["\\\x00-\x1f\x7f]', lambda m: {
        '"': '\\"', "\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t"
    }.get(m[0], f"\\{ord(m[0]):03d}"), value) + '"'


def lua_table(lines):
    return "\n".join(["-- Generated by tools/localization.py. Edit the source catalogs.", "return {",
                      *lines, "}", ""])


def module(rows):
    return lua_table(f"    [{lua_string(key)}] = {value}," for key, value in rows)


def compile_catalog(root, tag, pot):
    path = root / "translations" / f"{tag}.po"
    po = polib.pofile(str(path), encoding="utf-8")
    categories, formula = PLURAL_RULES[LANGUAGES[tag]]
    fields = re.fullmatch(r"\s*nplurals\s*=\s*(\d+)\s*;\s*plural\s*=\s*([^;]+);?\s*",
                          po.metadata.get("Plural-Forms", ""))
    if po.metadata.get("Language") != tag or not fields or int(fields[1]) != len(categories):
        raise ValueError(f"Invalid Language or Plural-Forms header in {path.name}")
    expected = gettext.c2py(formula.partition("plural=")[2].rstrip(";"))
    actual = gettext.c2py(fields[2])
    if any(actual(n) != expected(n) for n in range(201)):
        raise ValueError(f"Plural indices do not match the runtime for {tag}")
    originals = {entry.msgctxt: entry for entry in pot}
    translated = {}
    included = set()
    for entry in po:
        if entry.obsolete:
            continue
        key = entry.msgctxt
        if not key or key in included or key not in originals:
            raise ValueError(f"Missing, duplicate or unknown translation context in {path.name}: {key}")
        included.add(key)
        original = originals[key]
        if (entry.msgid, entry.msgid_plural) != (original.msgid, original.msgid_plural) or (
                "".join(entry.comment.split()) != "".join(original.comment.split())):
            raise ValueError(f"Source text or translator note changed for {key}. Merge {path.name} with the POT.")
        if "fuzzy" in entry.flags:
            continue
        if original.msgid_plural:
            if any(index not in range(len(categories)) for index in entry.msgstr_plural):
                raise ValueError(f"Unexpected plural index for {key}")
            values = [entry.msgstr_plural.get(i) for i in range(len(categories))]
        else:
            values = [entry.msgstr]
        if not all(values):
            continue
        if any(arguments(text) != arguments(original.msgid) for text in values):
            raise ValueError(f"Translation placeholder mismatch for {key}")
        if original.msgid_plural:
            fields = [f"{name} = {lua_string(text)}" for name, text in zip(categories, values)]
            translated[key] = "{ " + ", ".join(fields) + " }"
        else:
            translated[key] = lua_string(values[0])
    # Settings can display every language's own name without loading its whole catalog.
    name = translated.get("ui.settings.language_name", lua_string(tag))
    return module((key, translated[key]) for key in originals if key in translated), name


def output(path, content, check):
    if check:
        if not path.is_file() or path.read_text(encoding="utf-8") != content:
            raise ValueError(f"Outdated or missing {path.name}. Run tools/localization.py.")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")


def generate(root=ROOT, *, check=False):
    pot, plurals = template(root)
    generated = {root / "translations/PaintManager.pot": str(pot),
                 root / LOCALES / "en_plurals.lua": module(
                     (key, "{ count = " + lua_string(count) + ", one = " + lua_string(singular) + " }")
                     for key, (count, singular) in plurals.items())}
    index = []
    for tag, rule in LANGUAGES.items():
        catalog, name = compile_catalog(root, tag, pot)
        generated[root / LOCALES / f"{tag}.lua"] = catalog
        index.append(f"    {{ code = {lua_string(tag)}, name = {name}, rule = {lua_string(rule)} }},")
    generated[root / LOCALES / "index.lua"] = lua_table(index)
    for path, content in generated.items():
        output(path, content, check)
    return len(pot)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify checked-in outputs without writing")
    parser.add_argument("--template", action="store_true", help="only export the POT, for a new msgmerge")
    args = parser.parse_args()
    if args.template:
        source, _ = template(ROOT)
        output(ROOT / "translations/PaintManager.pot", str(source), args.check)
    else:
        print(f"{generate(check=args.check)} player-interface messages")
