#!/usr/bin/env python3
# tracker.py + server.py + index.html + products.json -> tek dosya: BlackFridayTracker.py
# Çalıştır: python build_single.py
import os, json
DIR = os.path.dirname(os.path.abspath(__file__))
rd = lambda n: open(os.path.join(DIR, n), encoding="utf-8").read()
tracker = rd("tracker.py")
server = rd("server.py")
server = server.split("import tracker as T", 1)[1]          # başlık + import satırlarını at
server = server.replace("T.", "_T.")                          # T.x -> _T.x (aynı modül)
out = [
 "#!/usr/bin/env python3",
 "# Black Friday Tracker — TEK DOSYA sürümü. Bu dosyayı çalıştırın (çift tık ya da: python BlackFridayTracker.py),",
 "# tarayıcı http://localhost:8010 adresinde kendiliğinden açılır. Ek paket gerekmez (Python 3.8+).",
 "# Telegram bildirimi için çalıştırmadan önce TELEGRAM_TOKEN ve TELEGRAM_CHAT_ID ortam değişkenlerini verin",
 "# ya da aşağıdaki iki satırı doldurun:",
 "TELEGRAM_TOKEN = \"\"      # örn. \"123456:ABC-DEF...\"",
 "TELEGRAM_CHAT_ID = \"\"    # örn. \"987654321\"",
 "import os as _os",
 "if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID: _os.environ.setdefault('TELEGRAM_TOKEN', TELEGRAM_TOKEN); _os.environ.setdefault('TELEGRAM_CHAT_ID', TELEGRAM_CHAT_ID)",
 "",
 tracker.split("if __name__ == \"__main__\":", 1)[0],
 "import sys as _sys; _T = _sys.modules[__name__]",
 "EMBED_INDEX = " + json.dumps(rd("index.html"), ensure_ascii=False),
 "EMBED_CATALOG = " + json.dumps(json.loads(rd("products.json")), ensure_ascii=False),
 "import http.server, socketserver, urllib.parse, json, os, sys, threading, time",
 server,
]
open(os.path.join(DIR, "BlackFridayTracker.py"), "w", encoding="utf-8").write("\n".join(out))
print("yazıldı: BlackFridayTracker.py (%d KB)" % (os.path.getsize(os.path.join(DIR, "BlackFridayTracker.py")) // 1024))
