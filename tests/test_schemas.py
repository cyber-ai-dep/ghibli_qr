"""Schema behavior — CallbackRequest / TaskData parsing and result extraction."""

import json

from src.ghibli_portrait.models.schemas import (
    CallbackRequest,
    GhibliQRRequest,
    TaskData,
    TaskState,
)


def test_get_result_urls_standard_kie_format():
    data = TaskData(taskId="t1", resultJson=json.dumps({"resultUrls": ["https://a/x.jpg"]}))
    assert data.get_result_urls() == ["https://a/x.jpg"]


def test_get_result_urls_flux_format():
    data = TaskData(taskId="t1", resultJson=json.dumps({"info": {"resultImageUrl": "https://a/y.jpg"}}))
    assert data.get_result_urls() == ["https://a/y.jpg"]


def test_get_result_urls_none_when_missing():
    assert TaskData(taskId="t1").get_result_urls() is None


def test_callback_success_flags():
    cb = CallbackRequest(
        code=200,
        data=TaskData(taskId="t1", state=TaskState.SUCCESS, resultJson=json.dumps({"resultUrls": ["u"]})),
    )
    assert cb.is_success is True
    assert cb.is_failure is False


def test_callback_failure_flags():
    cb = CallbackRequest(code=501, data=TaskData(taskId="t1", state=TaskState.FAIL, failMsg="boom"))
    assert cb.is_failure is True
    assert cb.is_success is False


def test_ghibli_qr_request_accepts_camelcase_alias():
    req = GhibliQRRequest(imgUrl="https://a/in.jpg", url="https://example.com")
    assert req.img_url == "https://a/in.jpg"
    assert req.url == "https://example.com"
