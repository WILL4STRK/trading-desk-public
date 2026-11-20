"""The dashboard is one inline <script>. A top-level name declared twice (const, let, var, function, class) is a
SyntaxError that stops the whole page, and no Python test would notice. This scans for it."""
import re
import unittest

from desk.paths import STATIC

_IDENT = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_DECL = {"const", "let", "var", "function", "class"}
_REGEX_AFTER = set("(,=:[!&|?{};+-*%<>~^") | {""}
_REGEX_AFTER_WORDS = {"return", "typeof", "case", "in", "of", "delete", "void", "throw", "else", "do", "yield", "await"}


def _skip_string(js, i):
    q = js[i]
    i += 1
    while js[i] != q:
        i += 2 if js[i] == "\\" else 1
    return i + 1


def _skip_balanced(js, i):
    """i is just after an opening '{'; return the index after its matching '}'."""
    depth, prev, word = 1, "", ""
    while depth:
        j = _skip_other(js, i, prev, word)
        if j is not None:
            i, prev, word = j, ")", ""
            continue
        c = js[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        if not c.isspace():
            prev = c
            word = ""
        i += 1
    return i


def _skip_template(js, i):
    i += 1
    while js[i] != "`":
        if js[i] == "\\":
            i += 2
        elif js.startswith("${", i):
            i = _skip_balanced(js, i + 2)
        else:
            i += 1
    return i + 1


def _skip_other(js, i, prev, word):
    """If a string, template, comment or regex literal starts at i, return the index after it; else None."""
    c = js[i]
    if c in "'\"":
        return _skip_string(js, i)
    if c == "`":
        return _skip_template(js, i)
    if c == "/":
        n = js[i + 1] if i + 1 < len(js) else ""
        if n == "/":
            j = js.find("\n", i)
            return len(js) if j < 0 else j
        if n == "*":
            return js.index("*/", i + 2) + 2
        if prev in _REGEX_AFTER or word in _REGEX_AFTER_WORDS:
            i += 1
            in_class = False
            while js[i] != "/" or in_class:
                if js[i] == "\\":
                    i += 1
                elif js[i] == "[":
                    in_class = True
                elif js[i] == "]":
                    in_class = False
                i += 1
            i += 1
            while i < len(js) and (js[i].isalpha()):
                i += 1
            return i
    return None


def top_level_names(js):
    """Every name declared at the top level of a script, in order (duplicates kept)."""
    names, i, depth, prev, word, expect = [], 0, 0, "", "", False
    while i < len(js):
        j = _skip_other(js, i, prev, word)
        if j is not None:
            i, prev, word = j, ")", ""
            continue
        c = js[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0 and c == "," and expect is None:
            expect = True               # next declarator of a const/let/var statement
        elif depth == 0 and c == ";":
            expect = False
        m = _IDENT.match(js, i) if (c.isalpha() or c in "_$") else None
        if m:
            w = m.group(0)
            if depth == 0 and prev != "." and w in _DECL:
                expect = w                 # a name follows
                if w in ("const", "let", "var"):
                    expect = True
                word = w
                prev = "a"
                i = m.end()
                if w in ("function", "class"):
                    k = re.compile(r"\s*\*?\s*").match(js, i).end()
                    n = _IDENT.match(js, k)
                    if n:
                        names.append(n.group(0))
                        i = n.end()
                    expect = False
                continue
            if depth == 0 and expect is True:
                names.append(w)
                expect = None              # in a declaration: a top-level comma starts the next declarator
            word, prev = w, "a"
            i = m.end()
            continue
        if not c.isspace():
            prev = c
            word = ""
        i += 1
    return names


def duplicates(js):
    seen, dup = set(), []
    for n in top_level_names(js):
        if n in seen and n not in dup:
            dup.append(n)
        seen.add(n)
    return dup


class PageScript(unittest.TestCase):
    def test_scanner_finds_a_clash(self):
        js = "const a = 1, b = (x, y) => x;\nconst pctS = v => v;\nfunction f() { const pctS = 2; }\nconst pctS = (x, d = 2) => x;\n"
        self.assertEqual(duplicates(js), ["pctS"])

    def test_scanner_ignores_nested_strings_and_regexes(self):
        js = ('let S = null, drafts = {}, FILTER = "const x";\n'
              'const re = /const q = 1; [/]/g, t = `const z ${ {a: 1}.a } \\` const w`;\n'
              '// const S = 2\n/* let S */\nfunction g(a) { let S = a; return S / 2; }\n')
        self.assertEqual(top_level_names(js), ["S", "drafts", "FILTER", "re", "t", "g"])
        self.assertEqual(duplicates(js), [])

    def test_scanner_catches_function_vs_const(self):
        self.assertEqual(duplicates("function load() {}\nconst load = 1;\n"), ["load"])

    def test_index_html_has_no_duplicate_top_level_names(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        scripts = [s for s in re.findall(r"<script>(.*?)</script>", html, re.S)]
        self.assertTrue(scripts, "no inline <script> found in desk/static/index.html")
        js = max(scripts, key=len)
        names = top_level_names(js)
        self.assertGreater(len(names), 50, "the scanner found too few top-level names; it is probably confused")
        self.assertEqual(duplicates(js), [], "declared more than once at the top level of the page script")


if __name__ == "__main__":
    unittest.main()
