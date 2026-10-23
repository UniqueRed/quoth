"""Render article.md as a page to copy and paste into Medium.

Medium does not read Markdown, but it keeps headings, bold, italics, lists, links and code
blocks when you paste formatted text from a browser. Images can't be pasted reliably, so each
one becomes a highlighted placeholder that names the file to upload and its caption.

Usage: python article/build_paste.py   (then open article/medium-paste.html in Chrome)
"""

import html
import re
from pathlib import Path

import markdown

HERE = Path(__file__).parent
IMAGE = re.compile(r"^!\[(?P<alt>[^\]]*)\]\(images/(?P<file>[^)]+)\)\n(?:\*(?P<caption>[^*\n]+)\*\n)?", re.M)

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Medium Paste</title>
<style>
  body {{ margin: 0; background: #f6f7f9; color: #15161c; font: 16px/1.6 -apple-system, system-ui, sans-serif; }}
  .help {{ max-width: 760px; margin: 24px auto 0; padding: 16px 20px; background: #eef0ff; border: 1px solid #d9dbfd; border-radius: 12px; font-size: 14.5px; }}
  .help ol {{ margin: 6px 0 0; padding-left: 20px; }}
  .meta {{ max-width: 760px; margin: 16px auto 0; display: grid; gap: 8px; }}
  .meta div {{ background: #fff; border: 1px solid #e7e8ed; border-radius: 10px; padding: 10px 14px; }}
  .meta b {{ display: block; font-size: 12px; color: #6b6f7c; text-transform: uppercase; letter-spacing: .05em; }}
  button {{ font: inherit; font-weight: 600; padding: 8px 16px; border-radius: 9px; border: 0; background: #5b5cf0; color: #fff; cursor: pointer; margin-top: 10px; }}
  #status {{ margin-left: 10px; color: #15803d; font-weight: 600; }}
  #body {{ max-width: 760px; margin: 16px auto 48px; background: #fff; border: 1px solid #e7e8ed; border-radius: 12px; padding: 28px 36px;
           font-family: Georgia, serif; font-size: 19px; line-height: 1.6; }}
  #body h2 {{ font-family: -apple-system, system-ui, sans-serif; font-size: 26px; margin: 1.6em 0 .4em; }}
  #body pre {{ background: #f2f2f2; padding: 14px 16px; border-radius: 6px; font-size: 14px; overflow-x: auto; }}
  #body .image {{ background: #fff6e5; border: 2px dashed #f0b44c; padding: 10px 14px; font-family: -apple-system, system-ui, sans-serif; font-size: 15px; }}
</style>
</head>
<body>
<div class="help">
  <b>To paste into Medium</b>
  <ol>
    <li>In a new Medium story, paste the title and subtitle below into Medium's title and subtitle lines.</li>
    <li>Click <b>Copy article body</b>, click into the line under the subtitle in Medium, and paste (⌘V).</li>
    <li>For each yellow <b>IMAGE</b> line: click it, delete its text, press <b>+</b> then the image icon, and upload the named file from <code>article/images/</code>. Type the caption into the image's caption field.</li>
  </ol>
  <button id="copy">Copy article body</button><span id="status"></span>
</div>
<div class="meta">
  <div><b>Title</b>{title}</div>
  <div><b>Subtitle</b>{subtitle}</div>
  <div><b>Cover image</b>Upload <code>images/00-cover.png</code> as the first image; Medium uses it as the story's preview.</div>
</div>
<div id="body">
{body}
</div>
<script>
document.getElementById("copy").onclick = async () => {{
  const body = document.getElementById("body");
  const blob = (type, text) => new Blob([text], {{ type }});
  try {{
    await navigator.clipboard.write([new ClipboardItem({{
      "text/html": blob("text/html", body.innerHTML),
      "text/plain": blob("text/plain", body.innerText),
    }})]);
  }} catch {{
    const range = document.createRange();
    range.selectNodeContents(body);
    getSelection().removeAllRanges();
    getSelection().addRange(range);
    document.execCommand("copy");
  }}
  document.getElementById("status").textContent = "Copied. Now paste into Medium.";
}};
</script>
</body>
</html>
"""


def placeholder(match: re.Match) -> str:
    caption = f" — caption: {match['caption']}" if match["caption"] else ""
    return f'<p class="image">IMAGE: upload {html.escape(match["file"])}{html.escape(caption)}</p>\n\n'


def main() -> None:
    text = (HERE / "article.md").read_text(encoding="utf-8")
    title, subtitle, rest = re.match(r"# ([^\n]+)\n\n## ([^\n]+)\n\n(.*)", text, re.S).groups()
    # The cover goes in by hand as the first image; drop it from the body.
    rest = IMAGE.sub(lambda m: "" if m["file"] == "00-cover.png" else placeholder(m), rest)
    body = markdown.markdown(rest, extensions=["fenced_code"])
    page = PAGE.format(title=html.escape(title), subtitle=html.escape(subtitle), body=body)
    (HERE / "medium-paste.html").write_text(page, encoding="utf-8")
    print("wrote", HERE / "medium-paste.html")


if __name__ == "__main__":
    main()
