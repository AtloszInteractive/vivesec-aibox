"""The scope is an ACL boundary, so its edges are pinned down here.

Two properties matter most. First, a single-drive scope must behave exactly
like the drive-keyed model it replaces -- including the conversation key, or
every session spilled to disk by an earlier build would be orphaned. Second,
the scope must never be widened by the path it is asked about: naming a file in
another drive is how a caller would try to read across the boundary.

    python adapter/scope_test.py
"""
import base64
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import corpus  # noqa: E402
import scope  # noqa: E402

FINANCE = "/storage/drives/finance/"
HR = "/storage/drives/hr/"


def header_for(*paths):
    """Build a VVS-Other-Drives value the way the ViVeSecBox does."""
    blob = "\x00".join(paths).encode("utf-8")
    return base64.urlsafe_b64encode(blob).decode("ascii").rstrip("=")


class SingleDriveScopeTest(unittest.TestCase):
    """Without an entitlement list the scope is the active drive, and nothing
    observable may change."""

    def setUp(self):
        self.scope = scope.resolve(FINANCE)

    def test_scope_is_only_the_active_drive(self):
        self.assertEqual(len(self.scope), 1)
        self.assertFalse(self.scope.multi)
        self.assertEqual(self.scope.source, scope.SOURCE_SINGLE)

    def test_corpus_id_matches_the_drive_derivation(self):
        self.assertEqual(self.scope.corpus_id, corpus.corpus_id_of_drive(FINANCE))
        self.assertEqual(self.scope.corpus_ids,
                         (corpus.corpus_id_of_drive(FINANCE),))

    def test_conversation_key_is_unchanged(self):
        # The raw header value keyed sessions before the scope existed; if this
        # changes, users silently lose their spilled conversations.
        self.assertEqual(self.scope.session_scope, FINANCE)

    def test_invalid_drives_are_rejected(self):
        with self.assertRaises(ValueError):
            scope.resolve("")
        with self.assertRaises(ValueError):
            scope.resolve("/etc/passwd")


class ScopeBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.scope = scope.resolve(FINANCE)

    def test_paths_in_the_active_drive_are_inside(self):
        self.assertTrue(self.scope.contains_path("/storage/drives/finance/q2/report.pdf"))
        self.assertEqual(self.scope.corpus_id_for_path("/storage/drives/finance/a.pdf"),
                         corpus.corpus_id_of_drive(FINANCE))

    def test_another_drive_is_outside(self):
        self.assertFalse(self.scope.contains_path("/storage/drives/hr/salaries.xlsx"))
        with self.assertRaises(ValueError):
            self.scope.corpus_id_for_path("/storage/drives/hr/salaries.xlsx")

    def test_traversal_cannot_climb_out_of_a_drive(self):
        # Without this the prefix check alone would accept the path, because it
        # still starts with the scoped drive root.
        escape = "/storage/drives/finance/../hr/salaries.xlsx"
        self.assertFalse(self.scope.contains_path(escape))
        with self.assertRaises(ValueError):
            self.scope.corpus_id_for_path(escape)

    def test_dot_and_empty_segments_are_not_canonical(self):
        # The box resolves `/a/./b` to `/a/b`; accepting it would let a path
        # string dodge the folder ACLs keyed on the canonical form (E03).
        for path in ("/storage/drives/finance/./q2/report.pdf",
                     "/storage/drives/finance//q2/report.pdf",
                     "/storage/drives/finance/q2/./report.pdf"):
            self.assertFalse(self.scope.contains_path(path), path)
        self.assertTrue(self.scope.contains_path("/storage/drives/finance/q2/"))

    def test_non_drive_paths_are_outside(self):
        self.assertFalse(self.scope.contains_path("/etc/passwd"))
        self.assertFalse(self.scope.contains_path(""))
        self.assertFalse(self.scope.contains_path("report.pdf"))


class MultiDriveScopeTest(unittest.TestCase):
    def setUp(self):
        self.scope = scope.resolve(FINANCE, [HR])

    def test_active_drive_stays_first(self):
        self.assertTrue(self.scope.multi)
        self.assertEqual(self.scope.drive_roots,
                         (corpus.norm(FINANCE), corpus.norm(HR)))
        self.assertEqual(self.scope.corpus_id, corpus.corpus_id_of_drive(FINANCE))

    def test_corpus_ids_are_parallel_to_the_roots(self):
        self.assertEqual(self.scope.corpus_ids,
                         (corpus.corpus_id_of_drive(FINANCE),
                          corpus.corpus_id_of_drive(HR)))

    def test_both_drives_are_inside(self):
        self.assertTrue(self.scope.contains_path("/storage/drives/finance/a.pdf"))
        self.assertTrue(self.scope.contains_path("/storage/drives/hr/b.pdf"))
        self.assertEqual(self.scope.corpus_id_for_path("/storage/drives/hr/b.pdf"),
                         corpus.corpus_id_of_drive(HR))

    def test_a_drive_outside_the_list_is_still_refused(self):
        self.assertFalse(self.scope.contains_path("/storage/drives/legal/nda.pdf"))

    def test_the_active_drive_is_not_duplicated(self):
        # The box may list the active drive again; trailing slashes differ.
        repeated = scope.resolve(FINANCE, ["/storage/drives/finance", HR, HR])
        self.assertEqual(len(repeated), 2)

    def test_wider_scope_gets_its_own_conversation(self):
        # The assistant's earlier answers are grounding material, so a wider
        # scope must not continue a narrower scope's thread.
        single = scope.resolve(FINANCE)
        self.assertNotEqual(self.scope.session_scope, single.session_scope)
        self.assertTrue(self.scope.session_scope.startswith("scope:"))
        self.assertEqual(self.scope.session_scope,
                         scope.resolve(FINANCE, [HR]).session_scope)

    def test_scope_is_reported_for_audit(self):
        reported = self.scope.as_dict()
        self.assertEqual(reported["drives"],
                         [corpus.norm(FINANCE), corpus.norm(HR)])
        self.assertEqual(reported["source"], scope.SOURCE_LIST)


class OtherDrivesHeaderTest(unittest.TestCase):
    """docs/aibox_patch_0902.md: urlsafe base64 of NUL-separated UTF-8 paths."""

    def test_decodes_a_list(self):
        self.assertEqual(scope.decode_other_drives(header_for(HR, FINANCE)),
                         [HR, FINANCE])

    def test_decodes_a_single_entry(self):
        self.assertEqual(scope.decode_other_drives(header_for(HR)), [HR])

    def test_tolerates_padding_and_blank_entries(self):
        padded = base64.urlsafe_b64encode(
            ("%s\x00\x00%s" % (HR, FINANCE)).encode("utf-8")).decode("ascii")
        self.assertEqual(scope.decode_other_drives(padded), [HR, FINANCE])

    def test_survives_names_a_comma_separated_list_would_break(self):
        # The NUL separator is why the encoding was chosen: these names carry a
        # comma, a space and accents.
        odd = "/storage/drives/pénzügy, 2026/"
        self.assertEqual(scope.decode_other_drives(header_for(odd)), [odd])

    def test_absent_header_is_empty(self):
        self.assertEqual(scope.decode_other_drives(None), [])
        self.assertEqual(scope.decode_other_drives("   "), [])

    def test_malformed_values_are_rejected(self):
        with self.assertRaises(ValueError):
            scope.decode_other_drives("not base64!!")
        with self.assertRaises(ValueError):
            scope.decode_other_drives(
                base64.urlsafe_b64encode(b"\xff\xfe").decode("ascii"))


class ResolveRequestTest(unittest.TestCase):
    def test_header_widens_the_scope(self):
        resolved = scope.resolve_request(FINANCE, header_for(HR))
        self.assertEqual(resolved.drive_roots,
                         (corpus.norm(FINANCE), corpus.norm(HR)))
        self.assertEqual(resolved.source, scope.SOURCE_HEADER)

    def test_active_drive_repeated_in_the_header_is_collapsed(self):
        resolved = scope.resolve_request(FINANCE, header_for(FINANCE, HR))
        self.assertEqual(len(resolved), 2)
        self.assertEqual(resolved.corpus_id, corpus.corpus_id_of_drive(FINANCE))

    def test_no_header_keeps_the_single_drive(self):
        resolved = scope.resolve_request(FINANCE, None)
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved.source, scope.SOURCE_SINGLE)

    def test_malformed_header_degrades_instead_of_failing(self):
        # The header can only widen the scope, so a contract mismatch must not
        # take the box offline -- it falls back to the active drive and warns.
        warnings = []
        resolved = scope.resolve_request(FINANCE, "not base64!!",
                                         on_warning=warnings.append)
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved.source, scope.SOURCE_SINGLE)
        self.assertTrue(warnings)

    def test_paths_outside_the_drive_prefix_are_dropped(self):
        warnings = []
        resolved = scope.resolve_request(
            FINANCE, header_for("/etc", HR), on_warning=warnings.append)
        self.assertEqual(resolved.drive_roots,
                         (corpus.norm(FINANCE), corpus.norm(HR)))
        self.assertTrue(warnings)

    def test_entitlements_apply_only_without_a_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "entitlements.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"lars": [HR], "*": ["/storage/drives/public/"]}, fh)
            os.environ["ADAPTER_ENTITLEMENTS"] = path
            try:
                grants = scope.Entitlements.from_env()
            finally:
                os.environ.pop("ADAPTER_ENTITLEMENTS", None)

            from_file = scope.resolve_request(FINANCE, None, "lars",
                                              entitlements=grants)
            self.assertEqual(from_file.source, scope.SOURCE_ENTITLEMENTS)
            self.assertEqual(len(from_file), 3)

            # The box is the authority whenever it speaks.
            from_header = scope.resolve_request(FINANCE, header_for(HR), "lars",
                                                entitlements=grants)
            self.assertEqual(from_header.source, scope.SOURCE_HEADER)
            self.assertEqual(len(from_header), 2)

    def test_unknown_user_gets_only_the_shared_grant(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "entitlements.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"lars": [HR]}, fh)
            grants = scope.Entitlements(path)
            resolved = scope.resolve_request(FINANCE, None, "someone-else",
                                             entitlements=grants)
            self.assertEqual(len(resolved), 1)

    def test_missing_entitlements_file_grants_nothing(self):
        grants = scope.Entitlements("/no/such/entitlements.json")
        resolved = scope.resolve_request(FINANCE, None, "lars",
                                         entitlements=grants)
        self.assertEqual(len(resolved), 1)

    def test_all_drives_is_the_last_resort(self):
        resolved = scope.resolve_request(
            FINANCE, None, "lars", all_drives=lambda: [FINANCE, HR])
        self.assertEqual(resolved.source, scope.SOURCE_ALL_DRIVES)
        self.assertEqual(len(resolved), 2)


class NarrowScopeTest(unittest.TestCase):
    """The client chooses where to look; it never chooses what it may reach."""

    def setUp(self):
        self.scope = scope.resolve(FINANCE, [HR])

    def test_selection_narrows_the_search(self):
        narrowed = self.scope.narrow([HR])
        self.assertEqual(narrowed.drive_roots, (corpus.norm(HR),))
        self.assertEqual(narrowed.corpus_ids, (corpus.corpus_id_of_drive(HR),))

    def test_narrowing_keeps_the_active_drive_as_the_write_target(self):
        # Saving and the audit key follow the drive the user is in, not the
        # drives they happen to be searching.
        narrowed = self.scope.narrow([HR])
        self.assertEqual(narrowed.corpus_id, corpus.corpus_id_of_drive(FINANCE))
        self.assertEqual(narrowed.active_root, corpus.norm(FINANCE))

    def test_narrowing_starts_a_separate_conversation(self):
        # An answer grounded in both drives must not be reused as grounding
        # once the user excludes one of them.
        both = self.scope.session_scope
        only_hr = self.scope.narrow([HR]).session_scope
        only_fin = self.scope.narrow([FINANCE]).session_scope
        self.assertNotEqual(both, only_hr)
        self.assertNotEqual(only_hr, only_fin)

    def test_narrowing_to_the_active_drive_restores_its_thread(self):
        self.assertEqual(self.scope.narrow([FINANCE]).session_scope,
                         scope.resolve(FINANCE).session_scope)

    def test_an_unentitled_drive_is_refused(self):
        with self.assertRaises(ValueError):
            self.scope.narrow(["/storage/drives/legal/"])

    def test_empty_selection_means_no_narrowing(self):
        self.assertEqual(self.scope.narrow([]).drive_roots, self.scope.drive_roots)
        self.assertEqual(self.scope.narrow(None).drive_roots, self.scope.drive_roots)

    def test_selection_order_does_not_matter(self):
        self.assertEqual(self.scope.narrow([HR, FINANCE]).drive_roots,
                         self.scope.drive_roots)


class RebuildScopeTest(unittest.TestCase):
    """A queued job must run with the scope its request was narrowed to."""

    def test_rebuild_does_not_add_the_active_drive_back(self):
        # resolve() always leads with the active drive; a persisted job scope
        # must not be re-widened that way when the worker picks it up.
        restored = scope.rebuild(FINANCE, [corpus.norm(HR)])
        self.assertEqual(restored.drive_roots, (corpus.norm(HR),))

    def test_rebuild_keeps_the_active_drive_as_the_write_target(self):
        restored = scope.rebuild(FINANCE, [corpus.norm(HR)])
        self.assertEqual(restored.corpus_id, corpus.corpus_id_of_drive(FINANCE))

    def test_rebuild_round_trips_a_narrowed_scope(self):
        narrowed = scope.resolve(FINANCE, [HR]).narrow([HR])
        restored = scope.rebuild(FINANCE, list(narrowed.drive_roots))
        self.assertEqual(restored.drive_roots, narrowed.drive_roots)
        self.assertEqual(restored.session_scope, narrowed.session_scope)

    def test_missing_record_falls_back_to_the_active_drive(self):
        self.assertEqual(scope.rebuild(FINANCE, None).drive_roots,
                         (corpus.norm(FINANCE),))
        self.assertEqual(scope.rebuild(FINANCE, []).drive_roots,
                         (corpus.norm(FINANCE),))


if __name__ == "__main__":
    unittest.main(verbosity=2)
