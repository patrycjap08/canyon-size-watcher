import json, re, requests
from bs4 import BeautifulSoup
from pathlib import Path
import os



# ===== KONFIG =====
NTFY_TOPIC = "gosia-canyon-alert"  # <- Twój temat w apce ntfy
WATCH_SIZE = "2XS"                 # <- tylko ten rozmiar wywołuje alert
ALERT_ONLY_WHEN_AVAILABLE = True   # True = powiadamiaj tylko gdy 2XS -> available
# ===================
FORCE_ALERT = os.getenv("FORCE_ALERT") == "1"


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
HEADERS = {"User-Agent": "Mozilla/5.0 (CanyonSizeWatcher/1.0)"}
TIMEOUT = 25
SIZE_ORDER = ["2XS", "XS", "S", "M", "L", "XL", "2XL"]

def notify(title: str, message: str):
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={"Title": title, "Priority": "high"},
            timeout=10,
        )
    except Exception:
        pass

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
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text

def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def parse_size_statuses(html: str) -> dict:
    """
    Zwraca mapę { "2XS": "available"/"unavailable"/"unknown", ... }
    na podstawie klas przycisków rozmiarów.
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
    lines = []
    for s in SIZE_ORDER:
        st = statuses.get(s, "—")
        lines.append(f"{s}: {st}")
    return lines

def main():
    state = load_state()
    any_errors = False

    for t in TARGETS:
        try:
            html = get_html(t["url"])
            size_map = parse_size_statuses(html)
            if FORCE_ALERT:
                snapshot = "\n".join(sizes_snapshot_lines(size_map))
                msg = (
                    f"{t['name']} – FORCED ALERT (test)\n{t['url']}\n\n"
                    f"Aktualne rozmiary:\n{snapshot}"
                )
                print("[TEST] Wysyłam wymuszone powiadomienie ntfy")
                notify("🔔 TEST – wymuszone powiadomienie", msg)

            # log do Actions: pełna tabelka
            print(f"\n=== {t['name']} ===")
            for line in sizes_snapshot_lines(size_map):
                print(line)

            # stan obserwowanego rozmiaru
            new_val = size_map.get(WATCH_SIZE, "unknown")
            key = f"{t['name']}|{WATCH_SIZE}"
            prev_val = state.get(key)

            should_alert = False
            if prev_val is None:
                # pierwszy zapis – tylko zapamiętaj
                state[key] = new_val
            else:
                if new_val != prev_val:
                    if ALERT_ONLY_WHEN_AVAILABLE:
                        should_alert = (new_val == "available")
                    else:
                        should_alert = True

                    
                    state[key] = new_val

            if should_alert:
                snapshot = "\n".join(sizes_snapshot_lines(size_map))
                msg = (
                    f"{t['name']} – {WATCH_SIZE}: {prev_val} → {new_val}\n"
                    f"{t['url']}\n\n"
                    f"Aktualne rozmiary:\n{snapshot}"
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
