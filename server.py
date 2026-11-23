"""
İnternet Programcılığı - MCP Sunucusu Ödevi
Konu: Türkiye Deprem Bilgi Sunucusu

Bu sunucu, Claude'a gerçek zamanlı deprem verisi çeken araçlar (tool) ekler.
Veri kaynakları (ikisi de ücretsiz, API anahtarı gerektirmez):
  - USGS (ABD Jeoloji Araştırmaları Kurumu) deprem servisi
  - Open-Meteo şehir arama servisi (şehir adından koordinat bulmak için)

Kendi bilgisayarında çalıştırma:
    pip install -r requirements.txt
    python server.py
Adres: http://localhost:8000/mcp
"""

import os
from datetime import datetime, timedelta, timezone

import httpx
from fastmcp import FastMCP

mcp = FastMCP("Turkiye Deprem Bilgi Sunucusu")

USGS_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
SEHIR_ARAMA_URL = "https://geocoding-api.open-meteo.com/v1/search"

# Türkiye saati sabit olarak UTC+3'tür (2016'dan beri yaz/kış saati uygulanmıyor).
# Bu sayede Windows'ta ek kütüphane (tzdata) gerekmez.
TR_SAAT = timezone(timedelta(hours=3))

# Türkiye'yi kapsayan yaklaşık koordinat kutusu.
# Kutu komşu ülkeleri de kısmen kapsadığı için sonuçlar ayrıca filtrelenir.
TURKIYE = {"minlatitude": 35.5, "maxlatitude": 42.5, "minlongitude": 25.5, "maxlongitude": 45.0}


# ---------- Yardımcı fonksiyonlar ----------

def _depremleri_getir(parametreler: dict) -> list[dict]:
    """USGS servisinden deprem verisini çeker ve sade bir listeye dönüştürür."""
    parametreler = {"format": "geojson", **parametreler}
    cevap = httpx.get(USGS_URL, params=parametreler, timeout=20)
    cevap.raise_for_status()
    veri = cevap.json()

    depremler = []
    for olay in veri.get("features", []):
        ozellik = olay["properties"]
        boylam, enlem, derinlik = olay["geometry"]["coordinates"]
        zaman = datetime.fromtimestamp(ozellik["time"] / 1000, tz=timezone.utc).astimezone(TR_SAAT)
        depremler.append({
            "buyukluk": ozellik.get("mag"),
            "yer": ozellik.get("place"),
            "tarih_saat": zaman.strftime("%d.%m.%Y %H:%M"),
            "derinlik_km": round(derinlik, 1),
            "enlem": round(enlem, 3),
            "boylam": round(boylam, 3),
            "detay_linki": ozellik.get("url"),
        })
    return depremler


def _sadece_turkiye(depremler: list[dict]) -> list[dict]:
    """Yer bilgisinde Türkiye geçen depremleri bırakır (Yunanistan, İran vb. elenir)."""
    return [d for d in depremler if d["yer"] and ("Turkey" in d["yer"] or "Türkiye" in d["yer"])]


def _baslangic_tarihi(gun: int) -> str:
    """Bugünden 'gun' kadar önceki tarihi USGS'in istediği formatta verir."""
    return (datetime.now(timezone.utc) - timedelta(days=gun)).strftime("%Y-%m-%dT%H:%M:%S")


def _sehir_koordinati(sehir: str) -> dict:
    """Şehir adından enlem/boylam bulur (Türkiye içinde arar)."""
    cevap = httpx.get(
        SEHIR_ARAMA_URL,
        params={"name": sehir, "count": 1, "language": "tr", "countryCode": "TR"},
        timeout=20,
    )
    cevap.raise_for_status()
    sonuclar = cevap.json().get("results")
    if not sonuclar:
        raise ValueError(f"'{sehir}' adında bir şehir bulunamadı.")
    s = sonuclar[0]
    return {"ad": s["name"], "enlem": s["latitude"], "boylam": s["longitude"]}


# ---------- MCP araçları (tool) ----------

@mcp.tool
def son_depremler(min_buyukluk: float = 3.0, adet: int = 10) -> list[dict]:
    """Türkiye'de son 7 günde olan depremleri, en yeniden eskiye listeler.

    min_buyukluk: Listelenecek en küçük deprem büyüklüğü (varsayılan 3.0)
    adet: En fazla kaç deprem gösterileceği (varsayılan 10, en fazla 50)
    """
    depremler = _depremleri_getir({
        **TURKIYE,
        "starttime": _baslangic_tarihi(7),
        "minmagnitude": min_buyukluk,
        "limit": 500,
        "orderby": "time",
    })
    return _sadece_turkiye(depremler)[: max(1, min(adet, 50))]


@mcp.tool
def sehir_yakinindaki_depremler(sehir: str, yaricap_km: int = 100, gun: int = 30) -> dict:
    """Bir şehrin çevresindeki depremleri listeler (örnek: Bingöl, İzmir, Malatya).

    sehir: Türkiye'deki bir şehir adı
    yaricap_km: Şehir merkezinden kaç km çevreye bakılacağı (varsayılan 100)
    gun: Son kaç günün depremlerine bakılacağı (varsayılan 30, en fazla 365)
    """
    konum = _sehir_koordinati(sehir)
    depremler = _depremleri_getir({
        "latitude": konum["enlem"],
        "longitude": konum["boylam"],
        "maxradiuskm": max(10, min(yaricap_km, 500)),
        "starttime": _baslangic_tarihi(max(1, min(gun, 365))),
        "minmagnitude": 2.5,
        "limit": 20,
        "orderby": "time",
    })
    return {
        "sehir": konum["ad"],
        "yaricap_km": yaricap_km,
        "son_gun": gun,
        "deprem_sayisi": len(depremler),
        "depremler": depremler,
    }


@mcp.tool
def en_buyuk_depremler(gun: int = 30, adet: int = 5) -> list[dict]:
    """Türkiye'de son X günde olan en büyük depremleri büyükten küçüğe sıralar.

    gun: Son kaç güne bakılacağı (varsayılan 30, en fazla 365)
    adet: Kaç deprem gösterileceği (varsayılan 5, en fazla 20)
    """
    depremler = _depremleri_getir({
        **TURKIYE,
        "starttime": _baslangic_tarihi(max(1, min(gun, 365))),
        "minmagnitude": 2.5,
        "limit": 500,
        "orderby": "magnitude",
    })
    return _sadece_turkiye(depremler)[: max(1, min(adet, 20))]


@mcp.tool
def deprem_istatistigi(gun: int = 7) -> dict:
    """Türkiye'de son X gündeki depremleri büyüklük aralıklarına göre sayar.

    gun: Son kaç güne bakılacağı (varsayılan 7, en fazla 90)
    """
    gun = max(1, min(gun, 90))
    depremler = _sadece_turkiye(_depremleri_getir({
        **TURKIYE,
        "starttime": _baslangic_tarihi(gun),
        "minmagnitude": 2.5,
        "limit": 2000,
    }))
    araliklar = {"2.5 - 3.9": 0, "4.0 - 4.9": 0, "5.0 - 5.9": 0, "6.0 ve üzeri": 0}
    for d in depremler:
        m = d["buyukluk"] or 0
        if m >= 6:
            araliklar["6.0 ve üzeri"] += 1
        elif m >= 5:
            araliklar["5.0 - 5.9"] += 1
        elif m >= 4:
            araliklar["4.0 - 4.9"] += 1
        else:
            araliklar["2.5 - 3.9"] += 1
    en_buyuk = max(depremler, key=lambda d: d["buyukluk"] or 0) if depremler else None
    return {
        "son_gun": gun,
        "toplam_deprem": len(depremler),
        "buyukluge_gore": araliklar,
        "en_buyuk_deprem": en_buyuk,
    }


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    mcp.run(transport="http", host="0.0.0.0", port=port)
