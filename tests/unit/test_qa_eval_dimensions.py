"""เทสต์ตรรกะการวัดสองมิติใน qa_eval (Retrieval hit + Answer accuracy).

ทดสอบ property ล้วน ๆ ไม่ต้องมี server — ยืนยันว่า retrieval_status จำแนกถูก
ทุกกรณี เพราะเป็นตัวเลขที่จะเอาไปพรีเซนต์
"""

from __future__ import annotations

from katrag.eval.qa_eval import QAResult
from katrag.eval.qa_questions import QAItem

_ITEM = QAItem(
    qid="T1", level="easy", program="DSBA",
    question="คำถามทดสอบ", expected="เฉลย", reference="อ้างอิง",
    check=("คำสำคัญ",), forbid=("ห้าม",),
)


def _result(**kw) -> QAResult:
    r = QAResult(item=_ITEM)
    for k, v in kw.items():
        setattr(r, k, v)
    return r


class TestRetrievalStatus:
    def test_error_when_api_failed(self) -> None:
        r = _result(error="ConnectionError: boom")
        assert r.retrieval_status == "error"
        assert r.retrieval_ok is False
        assert r.retrieval_measurable is True

    def test_structured_path_counts_as_ok(self) -> None:
        r = _result(answered_from="structured")
        assert r.retrieval_status == "structured"
        assert r.retrieval_ok is True

    def test_hit_when_recall_positive(self) -> None:
        r = _result(
            answered_from="llm+evidence",
            expected_pages=[("doc", 5)],
            cite_recall=0.5,
        )
        assert r.retrieval_status == "hit"
        assert r.retrieval_ok is True

    def test_miss_when_recall_zero(self) -> None:
        r = _result(
            answered_from="llm+evidence",
            expected_pages=[("doc", 5)],
            cite_recall=0.0,
        )
        assert r.retrieval_status == "miss"
        assert r.retrieval_ok is False
        assert r.retrieval_measurable is True

    def test_no_gold_when_no_expected_pages(self) -> None:
        r = _result(answered_from="llm+evidence", expected_pages=[])
        assert r.retrieval_status == "no_gold"
        # no_gold ต้องไม่ถูกนับเป็นทั้งผ่านและตัวหาร (วัดไม่ได้)
        assert r.retrieval_ok is False
        assert r.retrieval_measurable is False


class TestAnswerAccuracyIndependentOfRetrieval:
    def test_retrieval_hit_but_answer_wrong(self) -> None:
        """เคสสำคัญ: ดึงหน้าถูก (มิติ 1 ผ่าน) แต่ตอบผิด (มิติ 2 ตก).

        นี่คือการวินิจฉัยที่การวัดสองมิติต้องแยกให้เห็น — ปัญหาอยู่ที่ LLM ไม่ใช่
        retrieval (ตรงกับกรณี H5 จริง)
        """
        r = _result(
            answer="ตอบผิดไม่มีคำสำคัญ",
            answered_from="llm+evidence",
            expected_pages=[("doc", 15)],
            cite_recall=1.0,
            check_miss=["คำสำคัญ"],
        )
        assert r.retrieval_ok is True       # มิติ 1 ผ่าน
        assert r.auto_pass is False         # มิติ 2 ตก

    def test_forbid_hit_fails_answer_but_not_retrieval(self) -> None:
        r = _result(
            answered_from="llm+evidence",
            expected_pages=[("doc", 1)],
            cite_recall=1.0,
            check_hit=["คำสำคัญ"],
            forbid_hit=["ห้าม"],
        )
        assert r.retrieval_ok is True
        assert r.auto_pass is False
