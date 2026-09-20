"""Unit tests for named_files.resolve — the cases seen on aibox-02 (2026-09-20)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import named_files  # noqa: E402
from meta import MetaMirror  # noqa: E402
import llm  # noqa: E402

IMI = "/storage/drives/Imi"
NORBI = "/storage/drives/Norbi"
XXL = "/storage/drives/XXL AI Drive"
SUMMIT = IMI + "/Kiállítási excelek/AI SUMMIT nevek.xlsx"
SIOFOK = IMI + "/Kiállítási excelek/Kórházszöv_konf_2026_Siófok.xlsx"
TESZT = NORBI + "/XLSX/TESZT.xlsx"
PLAYBOOK = XXL + "/ViVeSec_AI_Summit_Stand_Sales_Playbook_HU.docx"
VIDEO = XXL + "/UpHome_videolinkek.xlsx"
VIDEO2 = "/storage/drives/UpHome/Videós bemutató anyagok/UpHome_videolinkek.xlsx"


def mirror():
    m = MetaMirror()
    for d in (IMI, NORBI, XXL, "/storage/drives/UpHome"):
        m.upsert(d, False, None, None)
    for f in (SUMMIT, SIOFOK, TESZT, PLAYBOOK, VIDEO, VIDEO2):
        m.upsert(f, True, 1, 10)
    return m


ROOTS = [IMI, NORBI, XXL, "/storage/drives/UpHome"]


def resolve(q, roots=ROOTS):
    return named_files.resolve(mirror(), roots, q, fallback=llm.source_files(q))


class NamedFilesTest(unittest.TestCase):
    def test_space_in_filename_resolves_to_full_path(self):
        # The regex alone yields "nevek.xlsx", which matches nothing.
        self.assertEqual(llm.source_files("mit tartalmaz az AI SUMMIT nevek.xlsx"),
                         ["nevek.xlsx"])
        self.assertEqual(resolve("mit tartalmaz az AI SUMMIT nevek.xlsx"), [SUMMIT])

    def test_case_insensitive_and_extension_as_word(self):
        self.assertEqual(resolve("milyen adatokat tartalmaz a teszt xlsx?"), [TESZT])
        self.assertEqual(resolve("Mit tartalmaz a Teszt.XLSX fájl?"), [TESZT])

    def test_accented_name_with_underscores(self):
        self.assertEqual(resolve("foglald össze: Kórházszöv_konf_2026_Siófok.xlsx"),
                         [SIOFOK])

    def test_word_boundaries_prevent_partial_matches(self):
        # "ateszt.xlsx" is not TESZT.xlsx; "teszt" alone (no extension) is not a file.
        self.assertEqual(resolve("mi van az ateszt.xlsx fájlban?"), ["ateszt.xlsx"])
        self.assertEqual(resolve("ez csak egy teszt kérdés"), [])

    def test_same_basename_in_two_drives_returns_both(self):
        self.assertEqual(sorted(resolve("nyisd meg az UpHome_videolinkek.xlsx-et")),
                         sorted([VIDEO, VIDEO2]))

    def test_scope_limits_the_lookup(self):
        self.assertEqual(resolve("mit tartalmaz a TESZT.xlsx", roots=[IMI]),
                         ["TESZT.xlsx"])  # unresolved -> regex fallback kept

    def test_no_filename_means_no_filter(self):
        self.assertEqual(resolve("kik szerepelnek az AI Summit névlistában?"), [])

    def test_unknown_regex_name_kept_as_fallback(self):
        self.assertEqual(resolve("mi áll a szerződés_v3.pdf-ben?"), ["szerződés_v3.pdf"])

    def test_longest_basename_first(self):
        m = mirror()
        m.upsert(IMI + "/report v2.xlsx", True, 1, 1)
        m.upsert(IMI + "/Q1 report v2.xlsx", True, 1, 1)
        out = named_files.resolve(m, [IMI], "nézd meg a Q1 report v2.xlsx fájlt",
                                  fallback=llm.source_files("Q1 report v2.xlsx"))
        self.assertEqual(out[0], IMI + "/Q1 report v2.xlsx")
        self.assertIn(IMI + "/report v2.xlsx", out)

    def test_cap(self):
        m = MetaMirror()
        m.upsert(IMI, False, None, None)
        names = ["file%02d.pdf" % i for i in range(30)]
        for n in names:
            m.upsert(IMI + "/" + n, True, 1, 1)
        q = " ".join(names)
        self.assertEqual(len(named_files.resolve(m, [IMI], q)), named_files.MAX_FILES)


if __name__ == "__main__":
    unittest.main()
