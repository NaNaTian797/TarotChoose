"""从已冻结的正文生成邮件 HTML，不增加或改写牌义。"""

from html import escape


HEADINGS = {"牌面", "固定牌义", "牌面图案（按正向牌面描述）：", "本次朝向的固定牌义："}


def render_html(body):
    lines = body.splitlines()
    rendered = []
    for index, line in enumerate(lines):
        text = escape(line)
        if index in (0, 2) or line in HEADINGS:
            text = f"<strong>{text}</strong>"
        elif line.startswith("今日关键词："):
            text = "<strong>今日关键词：</strong>" + escape(line.removeprefix("今日关键词："))
        rendered.append(text)
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"></head>'
            '<body><div style="font-family:Arial,sans-serif;line-height:1.7">'
            + "<br>".join(rendered) + "</div></body></html>\n")
