# Macht die Autouse-Fixtures aus common.py für JEDE Testdatei aktiv — ein bloßes
# ``import common`` reicht pytest dafür nicht.
from common import _caches_leeren  # noqa: F401
