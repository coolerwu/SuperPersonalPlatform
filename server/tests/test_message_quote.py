import pytest
from server.domain.message_quote import quote_content

@pytest.mark.parametrize('original,excerpt', [('前文需要引用后文', '需要引用'), ('**重要** 的 `内容`', '重要 的 内容'), ('请看[文档](https://example.com)说明', '文档说明'), ('第一段\n\n第二段', '第一段\n第二段'), ('| 姓名 | 分数 |\n| --- | --- |\n| 张三 | 90 |', '姓名\t分数\n张三\t90')])
def test_selected_quote_keeps_only_visible_excerpt(original, excerpt):
    assert quote_content(original, excerpt) == excerpt

@pytest.mark.parametrize('excerpt', ['', '伪造的原文', '   '])
def test_selected_quote_rejects_unknown_text(excerpt):
    with pytest.raises(ValueError):
        quote_content('原消息', excerpt)

def test_full_quote_is_unchanged():
    assert quote_content('**完整原文**', None) == '**完整原文**'
