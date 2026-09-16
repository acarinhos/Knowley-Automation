import unittest
from datetime import datetime, timezone

from database.question_audit import build_question_audit


class QuestionAuditTests(unittest.TestCase):
    def test_groups_v110_and_later_by_period_and_taxonomy(self):
        now = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
        documents = [
            {"version": "1.0.3", "categories": ["Tarih"], "sub_categories": ["Antik"], "created_at": now},
            {"version": "1.1.0", "categories": ["Tarih"], "sub_categories": ["Antik"], "created_at": now},
            {"dbVersion": "1.2.0", "categories": ["Coğrafya"], "sub_categories": ["Ülkeler"], "created_at": "2026-09-14T10:00:00+00:00"},
            {"dbVersion": "1.1.0", "categories": ["Tarih"], "sub_categories": ["Antik"], "created_at": "2026-08-01T10:00:00+00:00"},
            {"dbVersion": "1.1.0", "categories": ["Spor"], "sub_categories": ["Futbol"]},
        ]
        audit = build_question_audit(documents, now=now)
        self.assertEqual(audit["scanned"], 5)
        self.assertEqual(audit["totals"], {"today": 1, "week": 2, "month": 2, "all_time": 4, "undated": 1})
        tarih = next(row for row in audit["rows"] if row["category"] == "Tarih")
        self.assertEqual(tarih["all_time"], 2)
        self.assertEqual(tarih["today"], 1)


if __name__ == "__main__":
    unittest.main()
