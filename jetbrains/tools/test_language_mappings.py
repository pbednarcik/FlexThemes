"""Regression tests for the language gate, especially false-green failure modes.

Run: python -m unittest discover -s tools -p test_language_mappings.py -v
"""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from built import requires_build
from language_mappings import (
    MappingError,
    attribute_roles,
    check_jar,
    check_scheme,
    expand_roles,
    registrations,
    validate_coverage,
    verified_roles,
)

FACTORY = (
    "com/intellij/openapi/editor/colors/TextAttributesKey.createTextAttributesKey:"
)
ROOT = Path(__file__).resolve().parents[1]


class LanguageMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = ROOT / "build/language-tests"
        self.scratch.mkdir(parents=True, exist_ok=True)
        self.contract = {
            "languages": {
                "Test": {
                    "sources": [
                        {"product": "IU", "jar": "syntax.jar", "class": "test.Colours"}
                    ],
                    "roles": {"type": ["TEST_TYPE"]},
                    "preserve": {"diagnostic": ["TEST_ERROR"]},
                }
            }
        }

    def test_registered_literals_only_including_wide_and_fallback(self) -> None:
        output = (
            "0: ldc #1 // String DECOY\n2: putstatic #2 // Field INFO:String;\n"
            "5: ldc #3 // String TEST_TYPE\n7: invokestatic #4 // Method "
            + FACTORY
            + "(String;)Key;\n"
            "10: ldc_w #8 // String TEST_PARAMETER\n13: getstatic #9 // Field PARAMETER:Key;\n"
            "16: invokestatic #4 // Method " + FACTORY + "(String;Key;)Key;\n"
        )
        self.assertEqual({"TEST_TYPE", "TEST_PARAMETER"}, registrations(output))

    def test_dynamic_registration_is_not_silently_skipped(self) -> None:
        output = (
            "0: ldc #1 // String TEST_TYPE\n2: invokestatic #4 // Method "
            + FACTORY
            + "(String;)Key;\n"
            "5: aload_0\n6: invokestatic #4 // Method " + FACTORY + "(String;)Key;\n"
        )
        with self.assertRaisesRegex(MappingError, "unsupported"):
            registrations(output)

    def test_explicit_null_fallback_registration(self) -> None:
        output = (
            "0: ldc_w #1 // String PUBLIC_REFERENCE\n3: aconst_null\n"
            "4: checkcast #2 // class com/intellij/openapi/editor/colors/TextAttributesKey\n"
            "7: invokestatic #4 // Method " + FACTORY + "(String;Key;)Key;\n"
        )
        self.assertEqual({"PUBLIC_REFERENCE"}, registrations(output))

    def test_unrelated_string_before_factory_is_not_a_key(self) -> None:
        output = (
            "0: ldc #1 // String DECOY\n2: invokestatic #2 // Method log:(String;)V\n"
            "5: aload_0\n6: invokestatic #4 // Method " + FACTORY + "(String;)Key;\n"
        )
        with self.assertRaisesRegex(MappingError, "unsupported"):
            registrations(output)

    def test_new_key_requires_review(self) -> None:
        with self.assertRaisesRegex(MappingError, "unreviewed=.*NEW_KEY"):
            validate_coverage(
                "Test",
                self.contract["languages"]["Test"],
                {"TEST_TYPE", "TEST_ERROR", "NEW_KEY"},
            )

    def test_removed_key_fails(self) -> None:
        with self.assertRaisesRegex(MappingError, "missing=.*TEST_TYPE"):
            validate_coverage(
                "Test", self.contract["languages"]["Test"], {"TEST_ERROR"}
            )

    def test_preserved_key_is_allowed(self) -> None:
        validate_coverage(
            "Test", self.contract["languages"]["Test"], {"TEST_TYPE", "TEST_ERROR"}
        )

    def test_conflicting_roles_fail(self) -> None:
        self.contract["languages"]["Test"]["roles"]["function"] = ["TEST_TYPE"]
        with self.assertRaisesRegex(MappingError, "conflicting"):
            attribute_roles(self.contract)

    def test_preserved_mapping_overlap_fails(self) -> None:
        self.contract["languages"]["Test"]["preserve"]["diagnostic"].append("TEST_TYPE")
        with self.assertRaisesRegex(MappingError, "overlap"):
            attribute_roles(self.contract)

    def test_canonical_key_order_cannot_omit_or_invent_a_mapping(self) -> None:
        self.contract["keyOrder"] = ["OTHER_KEY"]
        with self.assertRaisesRegex(MappingError, "key order"):
            attribute_roles(self.contract)

    def write_evidence(self, folder: Path) -> tuple[Path, Path]:
        contract_path = folder / "contract.json"
        contract_path.write_text(json.dumps(self.contract), encoding="utf-8", newline="\n")
        group = self.contract["languages"]["Test"]
        evidence_path = folder / "evidence.json"
        evidence_path.write_text(
            json.dumps(
                {
                    "contractSha256": hashlib.sha256(
                        contract_path.read_bytes()
                    ).hexdigest(),
                    "languages": {
                        "Test": [
                            {**group["sources"][0], "keys": ["TEST_TYPE", "TEST_ERROR"]}
                        ]
                    },
                }
            ),
            encoding="utf-8", newline="\n",
        )
        return contract_path, evidence_path

    def test_stale_contract_evidence_fails(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.scratch) as folder:
            contract_path, evidence_path = self.write_evidence(Path(folder))
            contract_path.write_text(
                json.dumps(self.contract, indent=2), encoding="utf-8", newline="\n"
            )
            with self.assertRaisesRegex(MappingError, "stale"):
                verified_roles(contract_path, evidence_path)

    def test_missing_product_declaration_fails(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.scratch) as folder:
            contract_path, evidence_path = self.write_evidence(Path(folder))
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["languages"]["Test"] = []
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8", newline="\n")
            with self.assertRaisesRegex(MappingError, "missing declaration"):
                verified_roles(contract_path, evidence_path)

    def test_expansion_replaces_inherited_language_colour_with_palette_anchor(
        self,
    ) -> None:
        scheme_map = {
            "semanticRoles": {"type": "CSHARP_TYPE"},
            "attributes": {
                "CSHARP_TYPE": {"FOREGROUND": "#5FCFC0"},
                "TEST_TYPE": {"FOREGROUND": "#17708A"},
            },
        }
        with tempfile.TemporaryDirectory(dir=self.scratch) as folder:
            contract_path, evidence_path = self.write_evidence(Path(folder))
            expand_roles(
                scheme_map, ET.fromstring("<scheme/>"), contract_path, evidence_path
            )
        self.assertEqual("#5FCFC0", scheme_map["attributes"]["TEST_TYPE"]["FOREGROUND"])
        self.assertEqual(
            "#5FCFC0", scheme_map["attributes"]["CSHARP_TYPE"]["FOREGROUND"]
        )
        self.assertNotIn("TEST_ERROR", scheme_map["attributes"])

    def test_source_expressions_and_waivers_do_not_claim_aliases_are_measured(
        self,
    ) -> None:
        scheme_map = {
            "semanticRoles": {"type": "ANCHOR"},
            "attributes": {
                "ANCHOR": {
                    "FOREGROUND": "vs:source",
                    "status": "measured",
                    "measured": "#5FCFC0",
                    "sample": "source capture",
                    "contrastWaiver": "faithful source palette",
                }
            },
        }
        with tempfile.TemporaryDirectory(dir=self.scratch) as folder:
            contract_path, evidence_path = self.write_evidence(Path(folder))
            expand_roles(
                scheme_map,
                ET.fromstring("<scheme/>"),
                contract_path,
                evidence_path,
                lambda expression: "#5FCFC0",
            )
        alias = scheme_map["attributes"]["TEST_TYPE"]
        self.assertEqual("#5FCFC0", alias["FOREGROUND"])
        self.assertEqual("inherited", alias["status"])
        self.assertEqual("faithful source palette", alias["contrastWaiver"])
        self.assertNotIn("measured", alias)

    def test_packaged_missing_and_wrong_colours_fail(self) -> None:
        root = ET.fromstring(
            '<scheme><attributes><option name="ANCHOR"><value><option name="FOREGROUND" value="5FCFC0"/></value></option></attributes></scheme>'
        )
        self.assertTrue(check_scheme(root, self.contract, {"type": "ANCHOR"}))
        option = ET.SubElement(root.find("attributes"), "option", {"name": "TEST_TYPE"})
        ET.SubElement(
            ET.SubElement(option, "value"),
            "option",
            {"name": "FOREGROUND", "value": "FFFFFF"},
        )
        self.assertTrue(check_scheme(root, self.contract, {"type": "ANCHOR"}))
        option.find("./value/option").set("value", "5fcfc0")
        self.assertEqual([], check_scheme(root, self.contract, {"type": "ANCHOR"}))

    @requires_build
    def test_real_pilot_artifact_and_representative_faults(self) -> None:
        # This integration test is mandatory after building the pilot. It reads the
        # actual packaged artifact and checks one corruption in every language.
        import zipfile

        from generate import load_map
        from language_mappings import read_contract

        profile = load_map(
            ROOT / "src/periwinklenight/scheme/PeriwinkleNight.scheme-map.json"
        )
        contract_path = ROOT / profile["languageMappings"]
        contract = read_contract(contract_path)
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        jar = ROOT / f"dist/periwinkle-night-{version}.jar"
        check_jar(
            jar, profile, contract_path, ROOT / "build/language-evidence.json", None
        )
        with zipfile.ZipFile(jar) as archive:
            root = ET.fromstring(archive.read("colorSchemes/PeriwinkleNight.xml"))
        for language, group in contract["languages"].items():
            with self.subTest(language=language):
                changed = copy.deepcopy(root)
                key = group["roles"]["type"][0]
                changed.find(
                    f"./attributes/option[@name='{key}']/value/option[@name='FOREGROUND']"
                ).set("value", "FFFFFF")
                self.assertTrue(
                    check_scheme(changed, contract, profile["semanticRoles"])
                )


if __name__ == "__main__":
    unittest.main()
