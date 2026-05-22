import threading
from concurrent.futures import ThreadPoolExecutor

from bs4 import BeautifulSoup

from agent_base import (BASE_URL, all_sites, find_trip_site,
                        get_chamaeleon_website_html)


def make_recommendation_preview(recommendation: str):
    """
    This is where we gather the preview information that is necessary for the preview.
    The frontend is still responsible for displaying the preview with nice HTML.

    The information we need is the trip title and the head image URL.
    """

    try:
        target = ""
        if "#" in recommendation:
            recommendation, _target = recommendation.split("#")
            target = "#" + _target
        site = find_trip_site(recommendation)
    except ValueError:
        print(f"Warning: No site found for recommendation '{recommendation}'")
        return None  # No site found for the recommendation

    try:
        html = get_chamaeleon_website_html(site)
        soup = BeautifulSoup(html, "html.parser")

        title_text = soup.find("title").get_text(strip=True).split("-")[0].strip() # type: ignore
        if len(title_text.split()) > 5:
            title_text = recommendation.split("/")[-1].replace("-ALL", "")
        image_url = soup.find("meta", property="og:image")["content"] # type: ignore

        return {"url": BASE_URL + site + target, "title": title_text, "image": image_url}
    except Exception as e:
        print(f"Error creating preview for {recommendation}: {e}")
        return None


def make_recommendation_previews_async(recommendations):
    """
    Create recommendation previews in parallel using ThreadPoolExecutor
    """
    if not recommendations:
        return []

    with ThreadPoolExecutor(max_workers=len(recommendations)) as executor:
        # Submit all preview generation tasks
        future_to_rec = {
            executor.submit(make_recommendation_preview, rec): rec
            for rec in recommendations
        }

        previews = []
        for future in future_to_rec:
            try:
                preview = future.result(timeout=5)  # 5 second timeout per preview
                if preview:
                    previews.append(preview)
            except Exception as e:
                rec = future_to_rec[future]
                print(f"Error creating preview for {rec}: {e}")

        return previews
