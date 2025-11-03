import json
import re
import os
import time
import requests
from bs4 import BeautifulSoup
from pathlib import Path

# ===== KONFIG Z ENV =====
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "gosia-canyon-alert")  # temat z apki ntfy
WATCH_SIZE = os.getenv("WATCH_SIZE", "2XS")                 # obserwowany rozmiar
ALERT_ONLY_WHEN_AVAILABLE = os.getenv("ALERT_ONLY_WHEN_AVAILABLE", "1") == "1"
FORCE_ALERT = os.getenv("FORCE_ALERT", "0") == "1"          # w testach: wyślij snapshot niezależnie od zmiany
SIMULATE_CHANGE = os.getenv("SIMULATE_CHANGE", "").strip()  # "available"/"unavailable" (TYLKO DO TESTU)
SIMULATE_ONLY_TARGET = os.getenv("SIMULATE_ONLY_TARGET", "").strip()  # np. "R138_P01" (opcjonalnie)
# ========================

TARGETS = [
    {
        "name": "Canyon Allroad R138_P01",
        "url": "https://www.canyon.com/pl-pl/rowery-szosowe/endurance-bikes/endurace/allroad/endurace-allroad/4164.html?dwvar_4164_pv_rahmenfarbe=R138_P01",
    },
    {
        "name": "Canyon Allroad R138_P02",
        "url": "https://www.canyon.com/pl-pl/rowery-szosowe/endurance-bikes/endurace/allroad/endurace-allroad/4164.html?dwvar_4164_pv_rahmenfarbe=R138_P02#configuration-anchor",
    },
]

STATE_FILE = Path("watch_state.json")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Connection": "keep-alive",
}
TIMEOUT = 25
SIZE_ORDER = ["2XS", "XS", "S", "M", "L", "XL", "2XL"]

def notify(title: str, message: str):
    # Nagłówki HTTP muszą być czystym ASCII, bez \r \n i bez spacji na początku.
    def safe_header(s: str) -> str:
        s = (s or "").replace("\r", " ").replace("\n", " ")
        s = " ".join(s.split())           # zbicie wielokrotnych spacji i obcięcie brzegów
        s = s.encode("ascii", "ignore").decode("ascii")  # wytnij emoji/diakrytyki
        return s or "Notification"

    hdrs = {"Title": safe_header(title), "Priority": "high"}
    try:
        r = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),  # treść może być UTF-8
            headers=hdrs,
            timeout=12,
        )
        print(f"[ntfy] HTTP {r.status_code} Title='{hdrs['Title']}'")
    except Exception as e:
        print(f"[ntfy] exception: {e}")


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def get_html(url: str) -> str:
    last_exc = None
    for i in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code >= 500:
                raise RuntimeError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r.text
        except Exception as e:
            last_exc = e
            print(f"[fetch] próba {i+1}/3 nieudana: {e}")
            time.sleep(1 + i)  # mały backoff
    raise last_exc

def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def parse_size_statuses(html: str) -> dict:
    """
    Zwraca mapę { "2XS": "available"/"unavailable"/"unknown", ... }
    na podstawie klas:
      - productConfiguration__selectVariant--purchasable
      - productConfiguration__selectVariant--unpurchasable
    i atrybutu data-product-size.
    """
    soup = BeautifulSoup(html, "html.parser")
    statuses = {}

    for btn in soup.select("button.productConfiguration__selectVariant"):
        size = btn.get("data-product-size")
        if not size:
            t = norm(btn.get_text(" ", strip=True))
            if t in SIZE_ORDER:
                size = t
        if not size:
            continue

        classes = " ".join(btn.get("class", [])).lower()
        if "productconfiguration__selectvariant--purchasable" in classes:
            statuses[size] = "available"
        elif "productconfiguration__selectvariant--unpurchasable" in classes:
            statuses[size] = "unavailable"
        else:
            statuses[size] = statuses.get(size, "unknown")

    return statuses

def sizes_snapshot_lines(statuses: dict) -> list:
    return [f"{s}: {statuses.get(s, '—')}" for s in SIZE_ORDER]

def main():
    # (opcjonalny) mały jitter, żeby nie trafiać zawsze w tę samą minutę
    # import random; time.sleep(random.randint(0, 10))

    state = load_state()
    any_errors = False

    for t in TARGETS:
        try:
            html = get_html(t["url"])
            size_map = parse_size_statuses(html)

            # log do Actions
            print(f"\n=== {t['name']} ===")
            for line in sizes_snapshot_lines(size_map):
                print(line)

            # TEST: wymuszony snapshot (tylko podgląd, niezależnie od zmiany)
            if FORCE_ALERT:
                snapshot = "\n".join(sizes_snapshot_lines(size_map))
                msg = f"{t['name']} – FORCED SNAPSHOT\n{t['url']}\n\n{snapshot}"
                notify("🔔 TEST – snapshot rozmiarów", msg)

            # Normalna logika zmian dla obserwowanego rozmiaru
            new_val = size_map.get(WATCH_SIZE, "unknown")

            # TEST: symulacja zmiany statusu (np. "available")
            if SIMULATE_CHANGE and (not SIMULATE_ONLY_TARGET or SIMULATE_ONLY_TARGET in t["name"]):
                print(f"[TEST] Overriding {WATCH_SIZE} for {t['name']} -> {SIMULATE_CHANGE}")
                new_val = SIMULATE_CHANGE

            key = f"{t['name']}|{WATCH_SIZE}"
            prev_val = state.get(key)

            should_alert = False
            if prev_val is None:
                # pierwszy zapis – bez alertu, tylko zapamiętujemy bazę
                state[key] = new_val
            else:
                if new_val != prev_val:
                    should_alert = (new_val == "available") if ALERT_ONLY_WHEN_AVAILABLE else True
                    state[key] = new_val

            if should_alert:
                snapshot = "\n".join(sizes_snapshot_lines(size_map))
                msg = (
                    f"{t['name']} – {WATCH_SIZE}: {prev_val} → {new_val}\n"
                    f"{t['url']}\n\nAktualne rozmiary:\n{snapshot}"
                )
                notify("🔔 Canyon 2XS zmiana dostępności", msg)

        except Exception as e:
            any_errors = True
            notify(f"Watcher błąd: {t['name']}", f"{t['url']}\n{e}")

    save_state(state)
    if any_errors:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
