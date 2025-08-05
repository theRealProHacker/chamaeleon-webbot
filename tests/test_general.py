import common as _

from agent_base import detect_recommendation_links


def test_recommendation_detection():
    text = "/Afrika/Namibia/Etosha#termine"

    assert detect_recommendation_links(text) == [
        "/Afrika/Namibia/Etosha#termine",
    ]
