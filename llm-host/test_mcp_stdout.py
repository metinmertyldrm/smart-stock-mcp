"""MCP sunucuları stdout'a yazamaz.

stdio taşımasında sunucunun STDOUT'u JSON-RPC kanalıdır. Oraya basılan her satır
protokolü bozar; kötü durumda çıktı gerçek bir cevapla aynı satırda birleşir ve
o cevap tamamen kaybolur. Teşhis çıktıları stderr'e gitmelidir.

Bu test, sunucu süreçlerinde çalışan dosyalarda stderr'e yönlendirilmemiş
print() çağrısı kalmadığını doğrular.
"""
import ast
import os
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SERVER_FILES = [
    os.path.join(BASE_DIR, "stock-mcp", "tools.py"),
    os.path.join(BASE_DIR, "stock-mcp", "services.py"),
    os.path.join(BASE_DIR, "stock-mcp", "models.py"),
    os.path.join(BASE_DIR, "marketplace-mcp", "tools.py"),
    os.path.join(BASE_DIR, "marketplace-mcp", "services.py"),
    os.path.join(BASE_DIR, "marketplace-mcp", "models.py"),
]


def stdout_prints(source, filename="<test>"):
    """stderr'e yönlendirilmemiş print() çağrılarının satır numaralarını döndürür."""
    tree = ast.parse(source, filename=filename)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "print"):
            continue
        redirected = any(
            keyword.arg == "file" and ast.unparse(keyword.value).endswith("stderr")
            for keyword in node.keywords
        )
        if not redirected:
            offenders.append(node.lineno)
    return offenders


class StdoutPurityTest(unittest.TestCase):
    def test_mcp_servers_do_not_write_to_stdout(self):
        for path in SERVER_FILES:
            if not os.path.exists(path):
                continue
            with self.subTest(dosya=os.path.relpath(path, BASE_DIR)):
                with open(path, encoding="utf-8") as handle:
                    offenders = stdout_prints(handle.read(), path)
                self.assertEqual(
                    offenders, [],
                    f"{path} içinde stdout'a yazan print() var (satır {offenders}). "
                    "MCP stdio'da stdout JSON-RPC kanalıdır; logger veya "
                    "print(..., file=sys.stderr) kullanın.")


class DetectorTest(unittest.TestCase):
    """Kontrolün kendisi doğru mu?"""

    def test_plain_print_is_flagged(self):
        self.assertEqual(stdout_prints('print("merhaba")'), [1])

    def test_stderr_print_is_allowed(self):
        self.assertEqual(stdout_prints('import sys\nprint("x", file=sys.stderr)'), [])

    def test_logger_calls_are_not_flagged(self):
        self.assertEqual(stdout_prints('logger.info("x")'), [])


if __name__ == "__main__":
    unittest.main()
