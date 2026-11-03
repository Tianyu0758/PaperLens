# 文件说明：验证新增的结构浏览、定向阅读、分页游标和活动版本检查。
# 构造临时数据库与 TEI，确认完整段落读取可避免文本块重叠，并拒绝无效原文位置。

import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
from app.db import Database
from app.repositories import PaperRepository
from app.paper_access import inspect, read


class PaperAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        db=Database(self.root/"test.db"); db.initialize()
        self.repo=PaperRepository(db)
        self.repo.create_processing("paper-a","hash","active")
        self.repo.replace_ingestion_chunks("paper-a","active",[
            {"chunk_id":"c1","section_path":"Methods","section_type":"methods","chunk_index":0,"text":"First evidence.","page_number":None},
            {"chunk_id":"c2","section_path":"Results","section_type":"results","chunk_index":1,"text":"Second evidence."}])
        with db.transaction() as conn:
            conn.execute("UPDATE papers SET status='ready', active_ingestion_id='active' WHERE paper_id='paper-a'")

    def tearDown(self): self.temp.cleanup()

    def test_structure_and_bounded_read_of_active_paper(self):
        info=inspect(self.repo,"paper-a",self.root)
        self.assertEqual(len(info["sections"]),2)
        sid=info["sections"][0]["section_id"]
        result=read(self.repo,"paper-a",self.root,section_id=sid,chunk_id=None,offset=0,limit=5,ingestion_id="active")
        self.assertEqual(result["results"][0]["text"],"First")
        self.assertEqual(result["next_offset"],5)
        self.assertIsNone(result["results"][0]["page_number"])

    def test_cursors_and_foreign_chunks_are_rejected(self):
        for args,code in [({"chunk_id":"unknown"},404),({"chunk_id":"c1","ingestion_id":"old"},409),
                           ({"chunk_id":"c1","offset":999},422),({},422)]:
            params={"section_id":None,"chunk_id":None,"offset":0,"limit":100,"ingestion_id":None}|args
            with self.assertRaises(HTTPException) as caught: read(self.repo,"paper-a",self.root,**params)
            self.assertEqual(caught.exception.status_code,code)

    def test_full_tei_read_avoids_chunk_overlap(self):
        (self.root/"paper-a.tei.xml").write_text('<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body><div><head>Methods</head><p>Complete paragraph without overlap.</p></div></body></text></TEI>',encoding="utf-8")
        info=inspect(self.repo,"paper-a",self.root)
        self.assertEqual(info["source"],"tei")
        result=read(self.repo,"paper-a",self.root,section_id=info["sections"][0]["section_id"],chunk_id=None,offset=0,limit=1000,ingestion_id=None)
        self.assertEqual(result["results"][0]["text"],"Complete paragraph without overlap.")


if __name__=="__main__": unittest.main()
