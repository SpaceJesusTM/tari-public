import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("document_import", str(ROOT / "workspace/bin/document-import"))
spec = importlib.util.spec_from_loader(loader.name, loader)
documents = importlib.util.module_from_spec(spec)
loader.exec_module(documents)


class DocumentTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".test-tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".test-tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inbox = self.root / "inbox"
        self.inbox.mkdir()
        env = patch.dict(os.environ, {"TARI_WORKSPACE": str(self.root)})
        env.start()
        self.addCleanup(env.stop)

    def test_explicit_import_preserves_provenance_without_memory(self):
        source = self.inbox / "reference.md"
        source.write_text("# Reference\nA user-provided claim.\n", encoding="utf-8")
        result = documents.import_document(source)
        metadata = result["document"]
        self.assertEqual(metadata["source_name"], source.name)
        self.assertEqual(metadata["source_path"], "inbox/reference.md")
        self.assertFalse(metadata["memory_saved"])
        self.assertTrue(metadata["untrusted_content"])
        stored = self.root / "documents" / metadata["id"]
        self.assertEqual((stored / "source.md").read_bytes(), source.read_bytes())
        self.assertFalse((self.root / "memory").exists())
        shown = documents.show_document(metadata["id"], 5)
        self.assertTrue(shown["truncated"])
        self.assertEqual(len(shown["text"]), 5)
        self.assertTrue(documents.import_document(source)["already_imported"])

    def test_outside_inbox_and_symlinks_are_rejected(self):
        source = self.root / "private.txt"
        source.write_text("not authorized for intake")
        with self.assertRaises(documents.DocumentError):
            documents.import_document(source)
        link = self.inbox / "link.txt"
        link.symlink_to(source)
        with self.assertRaises(documents.DocumentError):
            documents.import_document(link)
        directory = self.inbox / "linked"
        directory.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(documents.DocumentError):
            documents.import_document(directory / "private.txt")

    def test_list_is_bounded_and_reads_provenance_only(self):
        self.assertEqual(documents.list_documents()["documents"], [])
        for number in range(2):
            source = self.inbox / (str(number) + ".txt")
            source.write_text("Source " + str(number))
            documents.import_document(source)
        listing = documents.list_documents(1)
        self.assertEqual(len(listing["documents"]), 1)
        self.assertTrue(listing["truncated"])
        self.assertEqual(listing["total"], 2)
        self.assertNotIn("text", listing["documents"][0])
        with self.assertRaises(documents.DocumentError):
            documents.list_documents(101)

    def test_symlink_storage_is_rejected(self):
        source = self.inbox / "source.txt"
        source.write_text("allowed input")
        redirected = self.root / "redirected"
        redirected.mkdir()
        (self.root / "documents").symlink_to(redirected, target_is_directory=True)
        with self.assertRaises(documents.DocumentError):
            documents.import_document(source)
        self.assertEqual(list(redirected.iterdir()), [])

    def test_bounded_input_and_binary_text(self):
        source = self.inbox / "source.txt"
        source.write_bytes(b"x" * 100)
        with patch.object(documents, "MAX_BYTES", 20):
            with self.assertRaises(documents.DocumentError):
                documents.import_document(source)
        with self.assertRaises(documents.DocumentError):
            documents.extract(b"binary\x00content", ".txt")
        with self.assertRaises(documents.DocumentError):
            documents.extract(b"\xff\xfe", ".md")

    def test_docx_extraction_uses_no_archive_paths_or_external_entities(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>Meeting notes</w:t></w:r></w:p></w:document>')
            archive.writestr("../../unexpected", "ignored")
        self.assertEqual(documents.extract(stream.getvalue(), ".docx"), "Meeting notes")
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("word/document.xml", '<!DOCTYPE x [<!ENTITY e SYSTEM="file:///private">]><x>&e;</x>')
        with self.assertRaises(documents.DocumentError):
            documents.extract(stream.getvalue(), ".docx")

    def test_pdf_uses_stdin_bounded_pages_and_rejects_scans(self):
        content = b"Extracted PDF text"
        def worker(*args, **kwargs):
            kwargs["stdout"].write(content)
            return subprocess.CompletedProcess([], 0)
        with patch.object(documents.shutil, "which", return_value="/usr/bin/pdftotext"), \
                patch.object(documents.subprocess, "run", side_effect=worker) as run:
            self.assertEqual(documents.extract(b"%PDF-1.7 example", ".pdf"), "Extracted PDF text")
            self.assertEqual(run.call_args.args[0][-2:], ["-", "-"])
            self.assertIn("200", run.call_args.args[0])
            self.assertEqual(run.call_args.kwargs["input"], b"%PDF-1.7 example")
            self.assertEqual(run.call_args.kwargs["preexec_fn"], documents.pdf_limits)
            content = b"\n\n"
            with self.assertRaises(documents.DocumentError):
                documents.extract(b"%PDF-1.7 example", ".pdf")

    def test_missing_optional_pdf_tool_and_bad_id_are_clear_failures(self):
        with patch.object(documents.shutil, "which", return_value=None):
            with self.assertRaisesRegex(documents.DocumentError, "pdftotext"):
                documents.extract(b"%PDF-1.7 example", ".pdf")
        with self.assertRaises(documents.DocumentError):
            documents.show_document("../../private", 10)


if __name__ == "__main__":
    unittest.main()
