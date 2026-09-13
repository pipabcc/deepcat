"""上游通用错误、额度限制及原始错误说明不能互相混淆。"""

import pytest

from deepcat import chatgpt_web2api as web


def test_stream_limit_error_keeps_the_upstream_reason():
    with pytest.raises(web.ChatGPTWebUpstreamError) as caught:
        web._raise_classified_upstream_error("You've hit your limit. Please try again later.")
    assert caught.value.error_code == "quota_or_rate_limited"
    assert "You've hit your limit" in str(caught.value)
    assert "账号、模型和思考档位" in str(caught.value)


def test_try_again_later_alone_is_not_proof_of_a_quota_error():
    with pytest.raises(web.ChatGPTWebUpstreamError) as caught:
        web._raise_classified_upstream_error("Something went wrong. Please try again later.")
    assert caught.value.error_code == "upstream_error"
    assert caught.value.status_code == 502


def test_structured_error_preserves_code_and_message_without_extra_payload():
    with pytest.raises(web.ChatGPTWebUpstreamError) as caught:
        web._raise_classified_upstream_error(
            {
                "code": "model_cap",
                "message": "This model has reached its usage limit.",
                "request_headers": {"Authorization": "Bearer must-not-be-displayed"},
            }
        )
    message = str(caught.value)
    assert "model_cap" in message
    assert "This model has reached its usage limit." in message
    assert "must-not-be-displayed" not in message
