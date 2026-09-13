"""图片标记解析既保留真实图片，也不误处理普通链接和代码示例。"""

import pytest

from deepcat.ui.markdown_images import has_markdown_image, iter_markdown_images, replace_markdown_images
from deepcat.ui.markdown_renderer import MarkdownRenderer


@pytest.mark.parametrize(
    "markdown,source",
    [
        ("![图](https://images.example/opaque?id=1&size=large)", "https://images.example/opaque?id=1&size=large"),
        ("![图](https://images.example/a_(b).png)", "https://images.example/a_(b).png"),
        ('![图](https://images.example/a.png "标题 ) ( 标题")', "https://images.example/a.png"),
        ("![图](<https://images.example/a (b).png> '标题')", "https://images.example/a (b).png"),
        ("![图]( <https://images.example/a(b).png> )", "https://images.example/a(b).png"),
        (r"![图](https://images.example/a\(b\).png)", "https://images.example/a(b).png"),
        ("![图](file:///C:/images/a%20(b).png)", "file:///C:/images/a%20(b).png"),
        (r"![图](C:\图片\含 空格(1).png)", r"C:\图片\含 空格(1).png"),
        ("![图](data:image/png;base64,QUJDRA==)", "data:image/png;base64,QUJDRA=="),
        ("[generated image](https://images.example/opaque)", "https://images.example/opaque"),
        ("![图](https://images.example/a?x=1&amp;y=2)", "https://images.example/a?x=1&y=2"),
    ],
)
def test_supported_images_preserve_destination(markdown, source):
    images = list(iter_markdown_images(markdown))
    assert len(images) == 1
    assert images[0].source == source
    assert (images[0].start, images[0].end) == (0, len(markdown))


@pytest.mark.parametrize(
    "markdown",
    [
        "[新闻](https://example.com/article)",
        "[原图下载](https://example.com/image.png)",
        "![图](javascript:alert(1))",
        "![图](file:///C:/settings.json)",
        "![图](data:text/html;base64,QUJDRA==)",
        "![图](https://example.com/incomplete",
        r"\![图](https://example.com/a.png)",
        "`![示例](https://example.com/a.png)`",
        "```markdown\n![示例](https://example.com/a.png)\n```",
        "~~~markdown\n![示例](https://example.com/a.png)\n~~~",
        "```markdown\n![尚未关闭的代码](https://example.com/a.png)",
    ],
)
def test_non_images_and_code_are_not_loaded(markdown):
    assert not has_markdown_image(markdown)
    assert replace_markdown_images(markdown, lambda _image: "误处理") == markdown


def test_legacy_article_and_thumbnail_pair_only_displays_thumbnail():
    text = (
        "前文\n![generated image](https://medium.com/example/article)\n"
        "![generated image](https://images.openai.com/static/example)\n后文"
    )
    images = list(iter_markdown_images(text))
    assert [image.source for image in images] == ["https://images.openai.com/static/example"]
    assert replace_markdown_images(text, lambda _image: "[缩略图]") == "前文\n[缩略图]\n后文"


def test_distinct_images_and_intervening_text_are_preserved():
    text = (
        "![generated image](https://cdn.example/image.png)\n"
        "![generated image](https://images.openai.com/static/one)\n说明\n"
        "![generated image](https://cdn.example/no-extension)\n说明\n"
        "![generated image](https://images.openai.com/static/two)"
    )
    assert len(list(iter_markdown_images(text))) == 4


def test_network_image_html_escapes_attributes_without_creating_text_link():
    rendered = MarkdownRenderer.to_html("![图 & <标签>](https://images.example/a?x=1&y=2)")
    assert '<img src="https://images.example/a?x=1&amp;y=2"' in rendered
    assert 'alt="图 &amp; &lt;标签&gt;"' in rendered
    assert "<a href=" not in rendered


def test_long_data_image_remains_one_image():
    source = "data:image/png;base64," + "QUJD" * 100_000
    images = list(iter_markdown_images(f"![大图]({source})"))
    assert len(images) == 1
    assert images[0].source == source
