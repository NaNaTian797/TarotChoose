"""从已冻结的正文生成邮件 HTML，不增加或改写牌义。"""

from html import escape


HEADINGS = {"牌面", "固定牌义", "牌面图案（按正向牌面描述）：", "本次朝向的固定牌义：",
            "🖼️ 牌面", "📖 固定牌义｜中英对照", "💡 牌义解读", "❓ 今日一问"}
FIELD_PREFIXES = ("所属牌组：", "今日关键词：")


def render_html(body):
    lines = body.splitlines()
    rendered = []
    for index, line in enumerate(lines):
        text = escape(line)
        if index in (0, 2) or line in HEADINGS:
            text = f"<strong>{text}</strong>"
        elif line.startswith(FIELD_PREFIXES):
            prefix = next(p for p in FIELD_PREFIXES if line.startswith(p))
            text = "<strong>" + escape(prefix) + "</strong>" + escape(line.removeprefix(prefix))
        elif line.startswith("- ") and " — " in line:
            chinese, english = line[2:].split(" — ", 1)
            text = "- <strong>" + escape(chinese) + "</strong> — " + escape(english)
        rendered.append(text)
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"></head>'
            '<body><div style="font-family:Arial,sans-serif;line-height:1.7">'
            + "<br>".join(rendered) + "</div></body></html>\n")
