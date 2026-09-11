from deepcat.ui.markdown_renderer import MarkdownRenderer


def test_display_math_block_renders_without_raw_delimiters():
    html = MarkdownRenderer.to_html(r"$$\int_{a}^{b} f(x) \,dx = F(b) - F(a)$$")

    assert "$$" not in html
    assert "<img" not in html
    assert "∫" in html
    assert "<sup" in html
    assert "<sub" in html
    assert "align=\"center\"" in html


def test_inline_math_renders_and_code_stays_literal():
    html = MarkdownRenderer.to_html(r"code `$x$`, math $e^{i\pi}+1=0$")

    assert "<code" in html
    assert "$x$" in html
    assert "<img" not in html
    assert "π" in html
    assert "<sup" in html


def test_bracket_display_math_across_lines():
    html = MarkdownRenderer.to_html(
        "\\[\n"
        r"\int_{-\infty}^{\infty} e^{-x^2}\,dx = \sqrt{\pi}"
        "\n\\]"
    )

    assert r"\[" not in html
    assert r"\]" not in html
    assert "<img" not in html
    assert "∞" in html
    assert "√" in html


def test_aligned_environment_gets_display_treatment():
    html = MarkdownRenderer.to_html(
        r"$$\begin{aligned} a &= b + c \\ d &= e \end{aligned}$$"
    )

    assert r"\begin" not in html
    assert "align=\"center\"" in html
    assert "<img" not in html
    assert "<table" in html


def test_embedded_double_dollar_math_does_not_leak_delimiters():
    html = MarkdownRenderer.to_html(r"formula: $$e^{i\pi}+1=0$$ done")

    assert "$$" not in html
    assert "<img" not in html
    assert "π" in html


def test_internal_image_preview_links_are_kept():
    html = MarkdownRenderer.to_html("[图片: demo.png](deepcat-image-preview:img_123)")
    assert 'href="deepcat-image-preview:img_123"' in html


def test_data_image_markdown_renders_as_image_tag():
    html = MarkdownRenderer.to_html("![generated image](data:image/png;base64,QUJDRA==)")

    assert "<img" in html
    assert 'src="data:image/png;base64,QUJDRA=="' in html
    assert 'width="640"' in html
    assert "![generated image]" not in html


def test_file_image_markdown_renders_as_image_tag(tmp_path):
    image_path = tmp_path / "generated.png"
    image_path.write_bytes(b"png")
    image_url = image_path.as_uri()

    html = MarkdownRenderer.to_html(f"![generated image]({image_url})")

    assert "<img" in html
    assert f'src="{image_url}"' in html
    assert "![generated image]" not in html


def test_file_image_markdown_accepts_unescaped_windows_path():
    raw_path = r"D:\Sample Images\generated-images\chatgpt_web_file.png"

    html = MarkdownRenderer.to_html(f"![generated image]({raw_path})")

    assert "<img" in html
    assert 'src="D:\\Sample Images\\generated-images\\chatgpt_web_file.png"' in html
    assert "![generated image]" not in html


def test_private_citation_marker_falls_back_to_readable_text():
    html = MarkdownRenderer.to_html("隐形 AI \ue200cite\ue202turn0search9\ue201")

    assert "\ue200" not in html
    assert "\ue201" not in html
    assert "turn0search9" not in html
    assert "引用" not in html


def test_markdown_renderer_entities_urls_and_mangled_citations():
    # 测试实体清洗
    html1 = MarkdownRenderer.to_html("关于■entity☆[\"people\",\"Noam Shazeer\"]↩的介绍。")
    assert "Noam Shazeer" in html1
    assert "■entity" not in html1

    # 测试 URL 链接清洗为带箭头的链接
    html2 = MarkdownRenderer.to_html("官网：■url☆OpenAI☆https://openai.com↩。")
    assert '<a href="https://openai.com"' in html2
    assert "OpenAI ↗" in html2
    assert "■url" not in html2

    # 测试☆多重引用拆分 (在没有 url 的时候应该直接被抹除)
    html3 = MarkdownRenderer.to_html("一些参考■cite☆turn0news27☆turn0news29↩。")
    assert "turn0news27" not in html3
    assert "turn0news29" not in html3
    assert "■cite" not in html3

    # 测试 Unicode 码点形式在渲染器中的匹配与清洗
    html4 = MarkdownRenderer.to_html("关于\ue200entity\ue202[\"known_celebrity\",\"Satya Nadella\",\"Microsoft CEO\"]\ue201的介绍。")
    assert "Satya Nadella" in html4
    assert "\ue200" not in html4

    html5 = MarkdownRenderer.to_html("官网：\ue200url\ue202OpenAI\ue202https://openai.com\ue201。")
    assert '<a href="https://openai.com"' in html5
    assert "OpenAI ↗" in html5

    # 测试 navlist 控制标志在渲染时被直接抹除
    html6 = MarkdownRenderer.to_html("前置\ue200navlist\ue202今日AI热点新闻\ue202turn0news3,turn0news10\ue201正文。")
    assert "今日AI热点新闻" not in html6
    assert "turn0news3" not in html6
    assert "\ue200" not in html6
    assert "前置" in html6
    assert "正文" in html6


def test_timeline_component_tags_render_as_markdown():
    html = MarkdownRenderer.to_html(
        "<Timeline>\n"
        "{/ Reason: 时间线适合展示演化过程。 /}\n"
        '<TimelineEvent time="0 秒" title="大爆炸瞬间（奇点）">\n'
        "宇宙的大小为零，处于无限高的密度和温度状态。\n"
        "</TimelineEvent>\n"
        '<TimelineEvent time="约 10^-36 秒" title="暴涨阶段（Inflation）">\n'
        "宇宙经历了一次超光速的急剧膨胀。\n"
        "</TimelineEvent>\n"
        "</Timeline>"
    )

    assert "0 秒：大爆炸瞬间（奇点）" in html
    assert "宇宙的大小为零" in html
    assert "约 10^-36 秒：暴涨阶段（Inflation）" in html
    assert "宇宙经历了一次超光速" in html
    assert "Timeline" not in html
    assert "Reason" not in html


def test_timeline_component_tolerates_missing_event_close():
    html = MarkdownRenderer.to_html(
        "<Timeline>\n"
        '<TimelineEvent time="几秒钟 到 3分钟" title="粒子诞生与核合成">\n'
        "随着温度下降，夸克结合成了质子和中子。"
        '<TimelineEvent time="约 38 万年" title="宇宙第一缕光（复合时期）">\n'
        "宇宙从一片浓雾变得透明。\n"
        "</TimelineEvent>\n"
        "</Timeline>"
    )

    assert "几秒钟 到 3分钟：粒子诞生与核合成" in html
    assert "随着温度下降" in html
    assert "约 38 万年：宇宙第一缕光（复合时期）" in html
    assert "宇宙从一片浓雾变得透明" in html
    assert "TimelineEvent" not in html


def test_timeline_component_tags_inside_code_block_stay_literal():
    html = MarkdownRenderer.to_html(
        "```xml\n"
        '<TimelineEvent time="0 秒" title="示例">\n'
        "正文\n"
        "</TimelineEvent>\n"
        "```"
    )

    assert "&lt;TimelineEvent" in html
    assert "0 秒：示例" not in html


def test_sequence_component_tags_render_as_numbered_markdown():
    html = MarkdownRenderer.to_html(
        "<Sequence>\n"
        '<Step subtitle="通过微信官方渠道办理" title="进入申请入口">\n'
        "微信搜索并关注**“中国农业银行”**官方公众号。\n"
        "</Step>\n"
        '<Step title="选择卡面与卡号">\n'
        "在页面中选择你喜欢的卡面样式。\n"
        "</Step>\n"
        '<Step title="填写资料并选择网点">\n'
        "填写个人身份信息，并**选择方便的营业网点**作为领卡网点。\n"
        "</Step>\n"
        '<Step title="网点领卡并激活">\n'
        "携带本人身份证原件前往网点领卡。\n"
        "</Step>\n"
        "</Sequence>"
    )

    assert "Sequence" not in html
    assert "&lt;Step" not in html
    assert "1. 进入申请入口" in html
    assert "2. 选择卡面与卡号" in html
    assert "3. 填写资料并选择网点" in html
    assert "4. 网点领卡并激活" in html
    assert "通过微信官方渠道办理" in html
    assert "border-left:3px solid" in html
    assert "<b>“中国农业银行”</b>" in html
    assert "<b>选择方便的营业网点</b>" in html


def test_sequence_component_tolerates_missing_closing_tags_and_attrs():
    html = MarkdownRenderer.to_html(
        "<Sequence>\n"
        '<Step title="填写资料">\n'
        "填写姓名和身份证号。\n"
        "<Step subtitle=\"携带身份证原件\">\n"
        "前往网点领卡。"
    )

    assert "1. 填写资料" in html
    assert "填写姓名和身份证号" in html
    assert "2. 步骤 2" in html
    assert "携带身份证原件" in html
    assert "前往网点领卡" in html
    assert "&lt;Step" not in html


def test_sequence_component_tags_inside_code_block_stay_literal():
    code_blocks = []
    html = MarkdownRenderer.to_html(
        "```xml\n"
        '<Sequence><Step title="示例">正文</Step></Sequence>\n'
        "```",
        code_blocks_out=code_blocks,
    )

    assert code_blocks == ['<Sequence><Step title="示例">正文</Step></Sequence>']
    assert "&lt;Sequence&gt;" in html
    assert "1. 示例" not in html


def test_elicitations_component_tags_are_cleaned_and_deduped():
    message = "如果您想继续深入了解某条国内新闻的细节，可以点击下方方向："
    label = "了解2026医保目录初审通过的药品亮点"
    query = "2026年国家医保目录初审通过的557个药品中，有哪些备受关注的创新药或罕见病药？"
    html = MarkdownRenderer.to_html(
        f'<ElicitationsGroup message="{message}">\n'
        "{/ Reason: Procedural requirement to allow user to deep-dive. /}\n"
        f'<Elicitation label="{label}" query="{query}"/>\n'
        "区自律公约，平台已对该账号采取无限期封禁的处置。\n\n"
        f"{message}\n"
        f"{label}：{query}"
    )

    assert "ElicitationsGroup" not in html
    assert "Elicitation" not in html
    assert "Reason" not in html
    assert "区自律公约" in html
    assert html.count(message) == 1
    assert html.count(label) == 1


def test_loose_model_markdown_is_normalized_without_model_specific_rules():
    html = MarkdownRenderer.to_html(
        "###核心区别\n"
        "维度｜推测｜建议\n"
        "---｜：---：｜---\n"
        "兼容性｜较高｜优先验证\n"
        "-------- **重要提醒**"
    )

    assert "<h3" in html
    assert "核心区别" in html
    assert "<table" in html
    assert "维度" in html and "推测" in html and "建议" in html
    assert "<hr" in html
    assert "<b>重要提醒</b>" in html


def test_loose_markdown_normalizer_never_changes_fenced_code():
    code_blocks = []
    html = MarkdownRenderer.to_html(
        "```text\n###不是标题\n甲｜乙｜丙\n-------- 正文\n```",
        code_blocks_out=code_blocks,
    )

    assert code_blocks == ["###不是标题\n甲｜乙｜丙\n-------- 正文"]
    assert "<h3" not in html
