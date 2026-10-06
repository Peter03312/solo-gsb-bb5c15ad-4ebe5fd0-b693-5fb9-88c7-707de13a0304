"""差异审阅单元测试：分类逻辑、版本/项目校验、HTTP JSON 导出。"""
import json
import os
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
import urllib.parse
from http.server import ThreadingHTTPServer

import subtitle
from diff import diff_entries
from store import Store

SRT = (
    "1\n00:00:01,000 --> 00:00:02,000\n甲\n\n"
    "2\n00:00:03,000 --> 00:00:04,000\n乙\n\n"
    "3\n00:00:05,000 --> 00:00:06,000\n丙\n"
)


def e(uid, s, en, text):
    return {"uid": uid, "start_ms": s, "end_ms": en, "text": text}


class DiffEntryTests(unittest.TestCase):
    def test_classification(self):
        old = [
            e("same", 1000, 2000, "同"),
            e("moved", 2000, 3000, "移位"),
            e("changed", 3000, 4000, "原文"),
            e("gone", 4000, 5000, "被删"),
            e("both", 5000, 6000, "改且移"),
        ]
        new = [
            e("same", 1000, 2000, "同"),
            e("changed", 3000, 4500, "原文"),      # 仅终点变化
            e("moved", 2000, 3000, "移位"),
            e("both", 5001, 6001, "改且移改"),
            e("added", 6000, 6500, "新增"),
        ]
        d = diff_entries(old, new)
        self.assertEqual([x["uid"] for x in d["added"]], ["added"])
        self.assertEqual([x["uid"] for x in d["deleted"]], ["gone"])
        self.assertEqual([x["uid"] for x in d["modified"]], ["changed", "both"])
        self.assertEqual([x["uid"] for x in d["reordered"]], ["moved"])
        self.assertEqual(d["summary"],
                         {"added": 1, "deleted": 1, "modified": 2,
                          "reordered": 1, "unchanged": 1})
        # 变化明细含双方时间与文本
        ch = next(x for x in d["modified"] if x["uid"] == "changed")
        self.assertEqual(ch["changes"], ["end_ms"])
        self.assertEqual(ch["old"]["end_ms"], 4000)
        self.assertEqual(ch["new"]["end_ms"], 4500)
        self.assertEqual(ch["old"]["text"], ch["new"]["text"])
        # 改且移：归入内容变化，带位置，但不进“仅顺序变化”
        bo = next(x for x in d["modified"] if x["uid"] == "both")
        self.assertEqual((bo["old_index"], bo["new_index"]), (4, 3))
        # 仅顺序变化：双方内容一致，只记位置
        mo = d["reordered"][0]
        self.assertEqual((mo["old_index"], mo["new_index"]), (1, 2))
        self.assertEqual(mo["text"], "移位")

    def test_reorder_not_reported_as_modified(self):
        old = [e("a", 1, 2, "x"), e("b", 3, 4, "y")]
        new = [e("b", 3, 4, "y"), e("a", 1, 2, "x")]
        d = diff_entries(old, new)
        self.assertEqual(d["modified"], [])
        self.assertEqual(len(d["reordered"]), 2)

    def test_text_change_is_modified(self):
        d = diff_entries([e("a", 1, 2, "x")], [e("a", 1, 2, "y")])
        self.assertEqual(d["modified"][0]["changes"], ["text"])
        self.assertEqual(d["reordered"], [])

    def test_identical_snapshots(self):
        snap = [e("a", 1, 2, "x"), e("b", 3, 4, "y")]
        d = diff_entries(snap, [dict(x) for x in snap])
        self.assertEqual(d["summary"],
                         {"added": 0, "deleted": 0, "modified": 0,
                          "reordered": 0, "unchanged": 2})

    def test_empty(self):
        d = diff_entries([], [])
        self.assertEqual(d["summary"]["unchanged"], 0)
        self.assertEqual(d["added"], [])
        self.assertEqual(d["reordered"], [])


class StoreDiffTests(unittest.TestCase):
    def setUp(self):
        fd, self.db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(self.db)
        self.store = Store(self.db)
        self.pid = self.store.create_project("项目甲", "srt", subtitle.parse_srt(SRT))
        _, entries = self.store.head(self.pid)
        self.u1, self.u2, self.u3 = [x["uid"] for x in entries]

    def tearDown(self):
        os.unlink(self.db)

    def test_diff_ok(self):
        self.store.merge(self.pid, 1, [
            {"type": "update", "uid": self.u2, "text": "乙改",
             "start_ms": 3000, "end_ms": 4000}])
        r = self.store.diff_versions(self.pid, 1, self.pid, 2)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["project"]["name"], "项目甲")
        self.assertEqual((r["version_a"], r["version_b"]), (1, 2))
        self.assertEqual(r["summary"]["modified"], 1)

    def test_same_version(self):
        r = self.store.diff_versions(self.pid, 1, self.pid, 1)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["summary"]["unchanged"], 3)

    def test_missing_version_rejected(self):
        r = self.store.diff_versions(self.pid, 1, self.pid, 99)
        self.assertEqual(r["status"], "error")
        self.assertIn("v99", r["message"])
        self.assertIn("不存在", r["message"])

    def test_missing_project_rejected(self):
        r = self.store.diff_versions(999, 1, 999, 1)
        self.assertEqual(r["status"], "error")
        self.assertIn("项目", r["message"])

    def test_cross_project_rejected(self):
        pid2 = self.store.create_project("项目乙", "srt", subtitle.parse_srt(SRT))
        r = self.store.diff_versions(self.pid, 1, pid2, 1)
        self.assertEqual(r["status"], "error")
        self.assertIn("不同项目", r["message"])
        self.assertIn("项目甲", r["message"])
        self.assertIn("项目乙", r["message"])


class HttpDiffTests(unittest.TestCase):
    """真实 HTTP 往返：页面、JSON API 与 UTF-8 JSON 导出。"""

    @classmethod
    def setUpClass(cls):
        fd, cls.db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(cls.db)
        os.environ["DB_PATH"] = cls.db
        import app as app_mod
        cls.app = app_mod
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app_mod.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.store = app_mod.store
        srt = SRT.encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{cls.port}/projects?name="
            + urllib.parse.quote("diff测试.srt"),
            data=srt, method="POST")
        try:
            urllib.request.urlopen(req)
        except urllib.error.HTTPError:
            pass
        projects = cls.store.list_projects()
        cls.pid = projects[0]["id"]
        _, entries = cls.store.head(cls.pid)
        cls.u2 = entries[1]["uid"]
        cls.store.merge(cls.pid, 1, [
            {"type": "update", "uid": cls.u2, "text": "乙改",
             "start_ms": 3000, "end_ms": 4000}])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        os.unlink(cls.db)

    def _get(self, path):
        return urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}")

    def test_diff_page(self):
        with self._get(f"/projects/{self.pid}/diff?a=1&b=2") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode("utf-8")
        self.assertIn("版本差异审阅", html)
        self.assertIn("内容变化", html)
        self.assertIn("/projects/%d/diff-export?a=1&b=2" % self.pid, html)

    def test_diff_page_missing_version(self):
        try:
            self._get(f"/projects/{self.pid}/diff?a=1&b=99")
            self.fail("应返回 404")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 404)
            body = exc.read().decode("utf-8")
            self.assertIn("v99", body)

    def test_api_diff(self):
        with self._get(f"/api/projects/{self.pid}/diff?a=1&b=2") as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("application/json", resp.headers["Content-Type"])
            d = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(d["status"], "ok")
        self.assertEqual(d["project"]["name"], "diff测试")
        self.assertEqual(d["summary"]["modified"], 1)
        self.assertEqual(d["modified"][0]["old"]["text"], "乙")
        self.assertEqual(d["modified"][0]["new"]["text"], "乙改")

    def test_api_diff_bad_params(self):
        try:
            self._get(f"/api/projects/{self.pid}/diff?a=x&b=2")
            self.fail("应返回 400")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)

    def test_api_diff_project_missing(self):
        try:
            self._get("/api/projects/99999/diff?a=1&b=2")
            self.fail("应返回 404")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 404)

    def test_export_json_utf8(self):
        with self._get(f"/projects/{self.pid}/diff-export?a=1&b=2") as resp:
            self.assertEqual(resp.status, 200)
            ctype = resp.headers["Content-Type"]
            disp = resp.headers["Content-Disposition"]
            raw = resp.read()
        self.assertIn("application/json", ctype)
        self.assertIn("charset=utf-8", ctype)
        self.assertIn(".json", disp)
        payload = json.loads(raw.decode("utf-8"))  # 严格 UTF-8 可解码
        self.assertEqual(payload["version_a"], 1)
        self.assertEqual(payload["version_b"], 2)
        self.assertEqual(payload["project"]["name"], "diff测试")
        self.assertIn("summary", payload)
        self.assertSetEqual(
            set(payload["diff"].keys()),
            {"added", "deleted", "modified", "reordered"})
        # 直接含中文原文，且能被 UTF-8 编解码
        self.assertIn("diff测试", raw.decode("utf-8"))
        self.assertEqual(raw, json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))

    def test_export_missing_version(self):
        try:
            self._get(f"/projects/{self.pid}/diff-export?a=1&b=99")
            self.fail("应返回 404")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 404)

    def test_existing_export_still_works(self):
        # 兼容性：原 SRT / VTT 导出与状态接口不受影响
        with self._get(f"/projects/{self.pid}/export?version=1&format=srt") as resp:
            srt = resp.read().decode("utf-8")
        self.assertIn("00:00:01,000 --> 00:00:02,000", srt)
        self.assertIn("乙", srt)
        with self._get(f"/api/projects/{self.pid}/state") as resp:
            state = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(state["head_version"], 2)


if __name__ == "__main__":
    unittest.main()
