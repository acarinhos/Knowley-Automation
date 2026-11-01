import os
import unittest
from datetime import datetime, timezone
from unittest import mock

from database import question_admin as qa


class Snap:
    def __init__(self, doc_id, data):
        self.id, self._data, self.exists = doc_id, data, data is not None

    def to_dict(self):
        return self._data


class Ref:
    def __init__(self, store, doc_id):
        self.store, self.doc_id = store, doc_id
        self.updated = None

    def get(self):
        return Snap(self.doc_id, self.store.get(self.doc_id))

    def update(self, data):
        self.store.setdefault("__updates__", []).append((self.doc_id, data))

    def delete(self):
        self.store.pop(self.doc_id, None)


class Query:
    def __init__(self, store, docs, fields=None):
        self.store, self.docs, self.fields = store, docs, fields

    def _with(self, docs=None, fields=None):
        return Query(self.store, self.docs if docs is None else docs, self.fields if fields is None else fields)

    def order_by(self, field, *a, **k):
        if field != "created_at":
            return self
        dated = [d for d in self.docs if d[1].get("created_at") is not None]  # Firestore drops docs lacking the field
        return self._with(sorted(dated, key=lambda x: x[1]["created_at"], reverse=True))

    def select(self, paths):
        return self._with(fields={path.split(".")[0] for path in paths})

    def where(self, filter):
        op = {">=": lambda a, b: a >= b, "<": lambda a, b: a < b}[filter.op_string]
        return self._with([d for d in self.docs if op(d[1]["created_at"], filter.value)])

    def start_after(self, snap):
        if snap.to_dict() is not None and "created_at" not in snap.to_dict():
            raise KeyError("cursor snapshot lacks the order-by field")
        ids = [d[0] for d in self.docs]
        return self._with(self.docs[ids.index(snap.id) + 1:])

    def limit(self, n):
        return self._with(self.docs[:n])

    def get(self):
        def masked(data):
            return data if self.fields is None else {k: v for k, v in data.items() if k in self.fields}

        return [Snap(i, masked(d)) for i, d in self.docs]

    def count(self):
        from types import SimpleNamespace

        return SimpleNamespace(get=lambda: [[SimpleNamespace(value=len(self.docs))]])


class Db:
    def __init__(self, docs):
        self.store = dict(docs)

    def collection(self, name):
        db = self

        class Col(Query):
            def document(self, doc_id):
                return Ref(db.store, doc_id)

        return Col(self.store, [(i, d) for i, d in self.store.items() if i != "__updates__"])


def doc(n, category="Tarih", language="tr", day=1, difficulty="Orta"):
    return {
        "translations": {language: {"question": f"Soru {n} <b>", "options": {"A": "a", "B": "b", "C": "c", "D": "d"}}},
        "supported_languages": [language], "categories": [category], "sub_categories": [],
        "difficulty": difficulty, "dbVersion": "1.1.0", "correct_answer": "A",
        "created_at": datetime(2026, 9, day, tzinfo=timezone.utc),
    }


class QuestionAdminTests(unittest.TestCase):
    def setUp(self):
        qa.invalidate_count_cache()
        self.db = Db({f"q{i}": doc(i, "Tarih" if i % 2 else "Spor", day=i) for i in range(1, 11)})

    def test_pagination_and_cursor(self):
        page1 = qa.list_questions(self.db, {"limit": "4"})
        self.assertEqual([r["id"] for r in page1["items"]], ["q10", "q9", "q8", "q7"])
        page2 = qa.list_questions(self.db, {"limit": "4", "cursor": page1["next_cursor"]})
        self.assertEqual([r["id"] for r in page2["items"]], ["q6", "q5", "q4", "q3"])
        last = qa.list_questions(self.db, {"limit": "4", "cursor": page2["next_cursor"]})
        self.assertEqual([r["id"] for r in last["items"]], ["q2", "q1"])
        self.assertEqual(last["next_cursor"], "")

    def test_filters(self):
        self.assertEqual({r["category"] for r in qa.list_questions(self.db, {"category": "Spor"})["items"]}, {"Spor"})
        ranged = qa.list_questions(self.db, {"created_from": "2026-09-03", "created_to": "2026-09-05"})
        self.assertEqual([r["id"] for r in ranged["items"]], ["q5", "q4", "q3"])
        self.assertEqual(len(qa.list_questions(self.db, {"language": "en"})["items"]), 0)
        self.assertEqual(len(qa.list_questions(self.db, {"q": "SORU 7"})["items"]), 1)
        with self.assertRaises(ValueError):
            qa.list_questions(self.db, {"created_from": "bad"})
        with self.assertRaises(ValueError):
            qa.list_questions(self.db, {"collection": "users"})

    def test_writes_blocked_when_flag_off(self):
        with mock.patch.dict(os.environ, {"KNOWLEY_QUESTION_EDITS_ENABLED": "false"}):
            with self.assertRaises(qa.EditsDisabledError):
                qa.update_question(self.db, "q1", {"language": "tr", "question": "x"}, ["Tarih"], lambda c: [])
            with self.assertRaises(qa.EditsDisabledError):
                qa.delete_question(self.db, "q1")
        self.assertIn("q1", self.db.store)

    def test_update_allowlist_and_validation(self):
        with mock.patch.dict(os.environ, {"KNOWLEY_QUESTION_EDITS_ENABLED": "true"}):
            with mock.patch("firebase_admin.firestore.SERVER_TIMESTAMP", "TS"):
                result = qa.update_question(
                    self.db, "q1", {"language": "tr", "question": " Yeni ", "correct_answer": "b", "category": "Spor"},
                    ["Tarih", "Spor"], lambda c: [])
            _, written = self.db.store["__updates__"][0]
            self.assertEqual(written["translations.tr.question"], "Yeni")
            self.assertEqual(written["correct_answer"], "B")
            self.assertEqual(written["categories"], ["Spor"])
            self.assertEqual(written["updated_at"], "TS")
            self.assertNotIn("question_hash", written)
            self.assertIn("categories", result["updated_fields"])
            for bad in (
                {"question_hash": "x"}, {"language": "tr", "question": ""}, {"language": "xx", "question": "a"},
                {"language": "de", "question": "a"}, {"correct_answer": "Z"}, {"category": "Yok"},
                {"language": "tr", "options": {"A": "a"}}, {"sub_category": "x"},
            ):
                with self.assertRaises(ValueError, msg=bad):
                    qa.update_question(self.db, "q1", bad, ["Tarih"], lambda c: [])
            with self.assertRaises(KeyError):
                qa.update_question(self.db, "nope", {"category": "Tarih"}, ["Tarih"], lambda c: [])

    def test_delete_when_enabled(self):
        with mock.patch.dict(os.environ, {"KNOWLEY_QUESTION_EDITS_ENABLED": "1"}):
            self.assertTrue(qa.delete_question(self.db, "q1")["deleted"])
            with self.assertRaises(KeyError):
                qa.delete_question(self.db, "q1")
            with self.assertRaises(ValueError):
                qa.delete_question(self.db, "../x")

    def test_category_branches(self):
        multi = doc(50)
        multi["categories"] = ["Tarih", "Spor"]
        single = doc(51)
        single["category"] = ["Tarih"]
        self.db.store.update({"multi": multi, "single": single})
        with mock.patch.dict(os.environ, {"KNOWLEY_QUESTION_EDITS_ENABLED": "true"}):
            with self.assertRaises(ValueError):
                qa.update_question(self.db, "multi", {"category": "Spor"}, ["Tarih", "Spor"], lambda c: [])
            qa.update_question(self.db, "single", {"category": "Spor"}, ["Tarih", "Spor"], lambda c: [])
            _, written = self.db.store["__updates__"][-1]
            self.assertEqual(written["category"], ["Spor"])
            self.assertNotIn("sub_categories", written)  # empty sub_category must not wipe existing value
            with self.assertRaises(ValueError):
                qa.update_question(self.db, "q1", {"question": "x", "language": "tr"}, [], lambda c: [], collection="tfquestions")

    def test_count_matches_filters(self):
        count = lambda **p: qa.count_questions(self.db, p)
        self.assertEqual((count()["count"], count()["method"]), (10, "aggregation"))
        self.assertEqual(count(created_from="2026-09-03", created_to="2026-09-05")["count"], 3)
        self.assertEqual(count(category="Spor")["count"], 5)
        self.assertEqual(count(category="Spor", created_from="2026-09-03", created_to="2026-09-05")["count"], 1)
        self.assertEqual(count(category="Tarih", difficulty="Orta", q="soru 7")["count"], 1)
        self.assertEqual(count(difficulty="Zor")["count"], 0)
        self.assertEqual(count(language="en")["count"], 0)
        self.assertTrue(count(category="Spor")["exact"])
        for params in ({"category": "Spor"}, {"created_from": "2026-09-01"}, {"q": "soru"}):
            self.assertEqual(count(**params)["count"], len(qa.list_questions(self.db, {**params, "limit": 100})["items"]))
        qa.invalidate_count_cache()
        with mock.patch.object(qa, "COUNT_SCAN_CAP", 4):
            capped = count(category="Spor")
        self.assertFalse(capped["exact"])
        self.assertEqual(capped["scanned"], 4)
        with self.assertRaises(ValueError):
            count(difficulty="x")
    def test_count_equals_reachable_list_with_undated_docs(self):
        undated = doc(60, "Spor")
        del undated["created_at"]
        self.db.store["undated"] = undated
        qa.invalidate_count_cache()
        self.assertEqual(qa.count_questions(self.db, {})["count"], 10)  # aggregation path ignores the undated doc
        self.assertEqual(qa.count_questions(self.db, {"category": "Spor"})["count"], 5)  # scan path too
        self.assertEqual(len(qa.list_questions(self.db, {"category": "Spor", "limit": 100})["items"]), 5)

    def test_count_scan_pages_past_batch_boundary(self):
        for n in range(20, 60):
            self.db.store[f"x{n}"] = doc(n, "Spor", day=(n % 28) + 1)
        qa.invalidate_count_cache()
        with mock.patch.object(qa, "SCAN_BATCH", 1):  # batches of 5 -> many start_after hops
            result = qa.count_questions(self.db, {"category": "Spor", "created_from": "2026-09-01"})
        self.assertEqual(result["count"], 45)
        self.assertTrue(result["exact"])

    def test_count_cap_boundary_and_cache(self):
        qa.invalidate_count_cache()
        with mock.patch.object(qa, "COUNT_SCAN_CAP", 10):
            exact = qa.count_questions(self.db, {"category": "Spor"})  # exactly 10 docs == cap
        self.assertTrue(exact["exact"])
        qa.invalidate_count_cache()
        self.db.store["more"] = doc(70, "Spor", day=2)
        with mock.patch.object(qa, "COUNT_SCAN_CAP", 10):
            self.assertFalse(qa.count_questions(self.db, {"category": "Spor"})["exact"])
        qa.invalidate_count_cache()
        first = qa.count_questions(self.db, {"category": "Spor"})
        self.db.store["later"] = doc(71, "Spor", day=3)
        self.assertEqual(qa.count_questions(self.db, {"category": "Spor"}), {**first, "cached": True})
        with mock.patch.dict(os.environ, {"KNOWLEY_QUESTION_EDITS_ENABLED": "true"}):
            qa.delete_question(self.db, "q1")  # a write invalidates the cache
        self.assertEqual(qa.count_questions(self.db, {"category": "Spor"})["count"], first["count"] + 1)

    def test_stats_same_set_as_count_and_list(self):
        undated = doc(60)
        del undated["created_at"]
        self.db.store["undated"] = undated
        old = doc(61, "Spor", day=2)
        old["dbVersion"] = "1.0.2"
        old["supported_languages"] = ["tr", "en"]
        self.db.store["old"] = old
        qa.invalidate_count_cache()
        stats = qa.question_stats(self.db, {})
        self.assertEqual(stats["total"], qa.count_questions(self.db, {})["count"])
        self.assertEqual(stats["total"], 11)  # undated excluded, all versions included
        self.assertEqual(stats["versions"], ["1.1.0", "1.0.2"])
        record = next(r for r in stats["questions"] if r["id"] == "old")
        self.assertEqual((record["version"], record["created"], record["scope"], record["languages"]),
                         ("1.0.2", "2026-09-02", "global", ["tr", "en"]))
        self.assertTrue(stats["exact"])
        self.assertTrue(qa.question_stats(self.db, {})["cached"])
        with mock.patch.object(qa, "COUNT_SCAN_CAP", 5):
            qa.invalidate_count_cache()
            capped = qa.question_stats(self.db, {})
        self.assertEqual((capped["total"], capped["exact"]), (5, False))
        with self.assertRaises(ValueError):
            qa.question_stats(self.db, {"collection": "users"})


class HttpTests(unittest.TestCase):
    def setUp(self):
        import json
        import threading
        from http.server import ThreadingHTTPServer
        from types import SimpleNamespace

        import ui_server

        self.json = json
        self.db = Db({"q1": doc(1)})
        repo = SimpleNamespace(categories=lambda: ["Tarih"], countries=lambda: [{"code": "TR", "name": "Türkiye"}], subcategories=lambda c, n=None: [])
        self.patches = [
            mock.patch.object(ui_server, "get_manager", lambda: SimpleNamespace(repository=repo)),
            mock.patch.object(ui_server, "init_firebase", lambda: self.db),
            mock.patch.dict(os.environ, {"KNOWLEY_QUESTION_EDITS_ENABLED": "false"}),
        ]
        for patcher in self.patches:
            patcher.start()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), ui_server.KnowleyHandler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        for patcher in self.patches:
            patcher.stop()

    def call(self, method, path, body=None):
        import http.client

        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        data = self.json.dumps(body) if body is not None else None
        conn.request(method, path, body=data, headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        return response.status, self.json.loads(response.read() or b"{}")

    def test_writes_are_403_when_flag_off(self):
        self.assertEqual(self.call("PATCH", "/api/admin/questions/q1", {"category": "Tarih"})[0], 403)
        self.assertEqual(self.call("DELETE", "/api/admin/questions/q1")[0], 403)
        self.assertIn("q1", self.db.store)

    def test_stats_route(self):
        status, body = self.call("GET", "/api/admin/questions/stats")
        self.assertEqual((status, body["total"], body["categories"]), (200, 1, ["Tarih"]))
        self.assertEqual(body["countries"][0]["code"], "GL")

    def test_version_route(self):
        status, body = self.call("GET", "/api/admin/version")
        self.assertEqual((status, body["revision"]), (200, qa.API_REVISION))

    def test_count_route(self):
        status, body = self.call("GET", "/api/admin/questions/count")
        self.assertEqual((status, body["count"], body["exact"]), (200, 1, True))

    def test_list_works_and_errors_do_not_leak(self):
        status, body = self.call("GET", "/api/admin/questions?limit=5")
        self.assertEqual((status, [r["id"] for r in body["items"]]), (200, ["q1"]))
        self.assertEqual(self.call("GET", "/api/admin/questions?difficulty=x")[0], 400)
        with mock.patch("ui_server.init_firebase", side_effect=RuntimeError("secret-path")):
            status, body = self.call("GET", "/api/admin/questions")
        self.assertEqual(status, 503)
        self.assertNotIn("secret-path", self.json.dumps(body))


if __name__ == "__main__":
    unittest.main()
