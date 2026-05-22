from agent_base import (chamaeleon_website_tool_base,
                        get_chamaeleon_website_html)

url = "/Afrika/Aegypten"

website = get_chamaeleon_website_html(url)

markdown = chamaeleon_website_tool_base(url)

with open(f"website_{url.replace('/', '_')}.html", "w", encoding="utf-8") as f:
    f.write(website)

with open(f"markdown_{url.replace('/', '_')}.md", "w", encoding="utf-8") as f:
    f.write(markdown)