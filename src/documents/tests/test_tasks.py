import os
import shutil
import uuid
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import TestCase
from django.test import override_settings
from django.utils import timezone

from documents import tasks
from documents.data_models import ConsumableDocument
from documents.data_models import DocumentSource
from documents.models import Correspondent
from documents.models import Document
from documents.models import DocumentType
from documents.models import PaperlessTask
from documents.models import Tag
from documents.sanity_checker import SanityCheckFailedException
from documents.sanity_checker import SanityCheckMessages
from documents.signals.handlers import before_task_publish_handler
from documents.signals.handlers import task_failure_handler
from documents.tests.test_classifier import dummy_preprocess
from documents.tests.utils import DirectoriesMixin
from documents.tests.utils import DummyProgressManager
from documents.tests.utils import FileSystemAssertsMixin
from documents.tests.utils import SampleDirMixin


class TestIndexReindex(DirectoriesMixin, TestCase):
    def test_index_reindex(self):
        Document.objects.create(
            title="test",
            content="my document",
            checksum="wow",
            added=timezone.now(),
            created=timezone.now(),
            modified=timezone.now(),
        )

        tasks.index_reindex()

    def test_index_optimize(self):
        Document.objects.create(
            title="test",
            content="my document",
            checksum="wow",
            added=timezone.now(),
            created=timezone.now(),
            modified=timezone.now(),
        )

        tasks.index_optimize()


class TestClassifier(DirectoriesMixin, FileSystemAssertsMixin, TestCase):
    @mock.patch("documents.tasks.load_classifier")
    def test_train_classifier_no_auto_matching(self, load_classifier):
        tasks.train_classifier()
        load_classifier.assert_not_called()

    @mock.patch("documents.tasks.load_classifier")
    def test_train_classifier_with_auto_tag(self, load_classifier):
        load_classifier.return_value = None
        Tag.objects.create(matching_algorithm=Tag.MATCH_AUTO, name="test")
        tasks.train_classifier()
        load_classifier.assert_called_once()
        self.assertIsNotFile(settings.MODEL_FILE)

    @mock.patch("documents.tasks.load_classifier")
    def test_train_classifier_with_auto_type(self, load_classifier):
        load_classifier.return_value = None
        DocumentType.objects.create(matching_algorithm=Tag.MATCH_AUTO, name="test")
        tasks.train_classifier()
        load_classifier.assert_called_once()
        self.assertIsNotFile(settings.MODEL_FILE)

    @mock.patch("documents.tasks.load_classifier")
    def test_train_classifier_with_auto_correspondent(self, load_classifier):
        load_classifier.return_value = None
        Correspondent.objects.create(matching_algorithm=Tag.MATCH_AUTO, name="test")
        tasks.train_classifier()
        load_classifier.assert_called_once()
        self.assertIsNotFile(settings.MODEL_FILE)

    def test_train_classifier(self):
        c = Correspondent.objects.create(matching_algorithm=Tag.MATCH_AUTO, name="test")
        doc = Document.objects.create(correspondent=c, content="test", title="test")
        self.assertIsNotFile(settings.MODEL_FILE)

        with mock.patch(
            "documents.classifier.DocumentClassifier.preprocess_content",
        ) as pre_proc_mock:
            pre_proc_mock.side_effect = dummy_preprocess

            tasks.train_classifier()
            self.assertIsFile(settings.MODEL_FILE)
            mtime = os.stat(settings.MODEL_FILE).st_mtime

            tasks.train_classifier()
            self.assertIsFile(settings.MODEL_FILE)
            mtime2 = os.stat(settings.MODEL_FILE).st_mtime
            self.assertEqual(mtime, mtime2)

            doc.content = "test2"
            doc.save()
            tasks.train_classifier()
            self.assertIsFile(settings.MODEL_FILE)
            mtime3 = os.stat(settings.MODEL_FILE).st_mtime
            self.assertNotEqual(mtime2, mtime3)


class TestSanityCheck(DirectoriesMixin, TestCase):
    @mock.patch("documents.tasks.sanity_checker.check_sanity")
    def test_sanity_check_success(self, m):
        m.return_value = SanityCheckMessages()
        self.assertEqual(tasks.sanity_check(), "No issues detected.")
        m.assert_called_once()

    @mock.patch("documents.tasks.sanity_checker.check_sanity")
    def test_sanity_check_error(self, m):
        messages = SanityCheckMessages()
        messages.error(None, "Some error")
        m.return_value = messages
        self.assertRaises(SanityCheckFailedException, tasks.sanity_check)
        m.assert_called_once()

    @mock.patch("documents.tasks.sanity_checker.check_sanity")
    def test_sanity_check_warning(self, m):
        messages = SanityCheckMessages()
        messages.warning(None, "Some warning")
        m.return_value = messages
        self.assertEqual(
            tasks.sanity_check(),
            "Sanity check exited with warnings. See log.",
        )
        m.assert_called_once()

    @mock.patch("documents.tasks.sanity_checker.check_sanity")
    def test_sanity_check_info(self, m):
        messages = SanityCheckMessages()
        messages.info(None, "Some info")
        m.return_value = messages
        self.assertEqual(
            tasks.sanity_check(),
            "Sanity check exited with infos. See log.",
        )
        m.assert_called_once()


class TestBulkUpdate(DirectoriesMixin, TestCase):
    def test_bulk_update_documents(self):
        doc1 = Document.objects.create(
            title="test",
            content="my document",
            checksum="wow",
            added=timezone.now(),
            created=timezone.now(),
            modified=timezone.now(),
        )

        tasks.bulk_update_documents([doc1.pk])


class TestEmptyTrashTask(DirectoriesMixin, FileSystemAssertsMixin, TestCase):
    """
    GIVEN:
        - Existing document in trash
    WHEN:
        - Empty trash task is called without doc_ids
    THEN:
        - Document is only deleted if it has been in trash for more than delay (default 30 days)
    """

    def test_empty_trash(self):
        doc = Document.objects.create(
            title="test",
            content="my document",
            checksum="wow",
            added=timezone.now(),
            created=timezone.now(),
            modified=timezone.now(),
        )

        doc.delete()
        self.assertEqual(Document.global_objects.count(), 1)
        self.assertEqual(Document.objects.count(), 0)
        tasks.empty_trash()
        self.assertEqual(Document.global_objects.count(), 1)

        doc.deleted_at = timezone.now() - timedelta(days=31)
        doc.save()

        tasks.empty_trash()
        self.assertEqual(Document.global_objects.count(), 0)


class TestRetryConsumeTask(
    DirectoriesMixin,
    SampleDirMixin,
    FileSystemAssertsMixin,
    TestCase,
):
    @override_settings(CONSUMPTION_FAILED_DIR=Path(__file__).parent / "samples")
    def test_retry_consume(self):
        test_file = self.SAMPLE_DIR / "corrupted.pdf"
        temp_copy = self.dirs.scratch_dir / test_file.name
        shutil.copy(test_file, temp_copy)

        headers = {
            "id": str(uuid.uuid4()),
            "task": "documents.tasks.consume_file",
        }
        body = (
            # args
            (
                ConsumableDocument(
                    source=DocumentSource.ConsumeFolder,
                    original_file=str(temp_copy),
                ),
                None,
            ),
            # kwargs
            {},
            # celery stuff
            {"callbacks": None, "errbacks": None, "chain": None, "chord": None},
        )
        before_task_publish_handler(headers=headers, body=body)

        with mock.patch("documents.tasks.ProgressManager", DummyProgressManager):
            with self.assertRaises(Exception):
                tasks.consume_file(
                    ConsumableDocument(
                        source=DocumentSource.ConsumeFolder,
                        original_file=temp_copy,
                    ),
                )

        task_failure_handler(
            task_id=headers["id"],
            exception="Example failure",
        )

        task = PaperlessTask.objects.first()
        # Ensure the file is moved to the failed dir
        self.assertIsFile(settings.CONSUMPTION_FAILED_DIR / task.task_file_name)

        with mock.patch("documents.tasks.ProgressManager", DummyProgressManager):
            with self.assertLogs("documents.tasks", level="INFO") as cm:
                tasks.retry_failed_file(task_id=task.task_id, clean=True)
                self.assertIn("PDF cleaned successfully", cm.output[0])
